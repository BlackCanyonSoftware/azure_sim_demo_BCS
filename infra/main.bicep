// Azure side of the sim demo: everything in one resource group (plus one app registration in Entra ID), rebuilt or removed with
// one command.
//
//   browser ──sign-in (Entra ID)──► App Service web app (page + Python API, Free F1, managed identity, no keys)
//                   │ role: Service Bus Data Sender                ▲ 30-minute read links (user delegation SAS)
//                   ▼                                              │
//              Service Bus queue "sim-jobs" ──► KPrime worker (listen-only key, outgoing only)
//                                                    │ write-only container link
//                                                    ▼
//                                     Storage: container "results" (status, GIF, MP4, last frame)
//   Application Insights collects the web app's and the worker's logs and traces.

extension microsoftGraphV1 // lets Bicep create the Entra ID app registration too (see bicepconfig.json)

@description('Short name put in front of every resource name.')
@minLength(3)
@maxLength(10)
param prefix string = 'simdemo'

@description('Where everything lives. Azure for Students allows only some regions (see README).')
param location string = resourceGroup().location

@description('Entra ID object ids (comma-separated) of the accounts allowed to queue jobs.')
param operators string = ''

@description('Monthly budget in US dollars; an email goes out at 50%, 80% and 100% of it. 0 = no budget.')
param budgetUsd int = 5

@description('Who the budget emails go to (given at deploy time, never stored in the repo).')
param budgetEmail string = ''

@description('First day of the current month, yyyy-MM-01, for the budget.')
param budgetStart string = ''

var unique = uniqueString(resourceGroup().id)
var tags = { project: 'azure-sim-demo' }
// built-in role ids: https://learn.microsoft.com/azure/role-based-access-control/built-in-roles
var roles = {
  blobDataContributor: 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
  blobDelegator: 'db58b8e5-c6ad-4a2a-8342-4190687cbf4a'
  busDataSender: '69a216fc-b8fb-44d8-bc22-1f3c2cd27a39'
}

// ---------------------------------------------------------------- monitoring
resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${prefix}-logs-${unique}'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
    workspaceCapping: { dailyQuotaGb: 1 } // a runaway logger cannot spend the credit
  }
}

resource insights 'Microsoft.Insights/components@2020-02-02' = {
  name: '${prefix}-insights'
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logs.id
  }
}

// ---------------------------------------------------------------- storage for results
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: take('${prefix}${unique}', 24)
  location: location
  tags: tags
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    accessTier: 'Hot'
    allowBlobPublicAccess: false // nothing is readable without a signed link
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
  }
}

resource blobs 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource results 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobs
  name: 'results'
  properties: { publicAccess: 'None' }
}

resource tidy 'Microsoft.Storage/storageAccounts/managementPolicies@2023-05-01' = {
  parent: storage
  name: 'default'
  properties: {
    policy: {
      rules: [
        {
          name: 'delete-old-jobs'
          enabled: true
          type: 'Lifecycle'
          definition: {
            filters: { blobTypes: [ 'blockBlob' ], prefixMatch: [ 'results/jobs/' ] }
            actions: { baseBlob: { delete: { daysAfterModificationGreaterThan: 14 } } }
          }
        }
      ]
    }
  }
}

// ---------------------------------------------------------------- the job queue
resource bus 'Microsoft.ServiceBus/namespaces@2022-10-01-preview' = {
  name: '${prefix}-bus-${unique}'
  location: location
  tags: tags
  sku: { name: 'Basic', tier: 'Basic' } // queues only, cents per million operations
  properties: { minimumTlsVersion: '1.2' }
}

resource queue 'Microsoft.ServiceBus/namespaces/queues@2022-10-01-preview' = {
  parent: bus
  name: 'sim-jobs'
  properties: {
    lockDuration: 'PT5M' // the worker renews it while a job runs
    maxDeliveryCount: 3 // then the job goes to the dead-letter queue
    defaultMessageTimeToLive: 'PT2H' // a job nobody picks up in 2 hours is dropped
    deadLetteringOnMessageExpiration: true
  }
}

resource listenRule 'Microsoft.ServiceBus/namespaces/queues/authorizationRules@2022-10-01-preview' = {
  parent: queue
  name: 'worker-listen' // the home PC is not an Azure resource, so it gets a listen-only key (Azure Arc would remove it)
  properties: { rights: [ 'Listen' ] }
}

// ---------------------------------------------------------------- the page and its API
resource plan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: '${prefix}-plan'
  location: location
  tags: tags
  kind: 'linux'
  sku: { name: 'F1', tier: 'Free' } // free: 60 CPU minutes a day, sleeps when idle
  properties: { reserved: true } // reserved = Linux
}

resource site 'Microsoft.Web/sites@2023-12-01' = {
  name: '${prefix}-web-${unique}'
  location: location
  tags: tags
  kind: 'app,linux'
  identity: { type: 'SystemAssigned' } // the app's own Entra identity: the roles below are given to it, so it needs no keys
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.12'
      appCommandLine: 'gunicorn --bind=0.0.0.0 --timeout 120 --workers 2 app:app'
      alwaysOn: false // not available on Free
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      appSettings: [
        { name: 'SCM_DO_BUILD_DURING_DEPLOYMENT', value: 'true' } // install requirements.txt when the code is uploaded
        { name: 'JOBS_NAMESPACE', value: '${bus.name}.servicebus.windows.net' }
        { name: 'JOBS_QUEUE', value: queue.name }
        { name: 'RESULTS_ACCOUNT_URL', value: storage.properties.primaryEndpoints.blob }
        { name: 'RESULTS_CONTAINER', value: results.name }
        { name: 'OPERATORS', value: operators }
        { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: insights.properties.ConnectionString }
      ]
    }
  }
}

// Code is uploaded with an Entra sign-in (az webapp deploy), so the old username/password publishing is switched off.
resource noScmPassword 'Microsoft.Web/sites/basicPublishingCredentialsPolicies@2023-12-01' = {
  parent: site
  name: 'scm'
  properties: { allow: false }
}

resource noFtpPassword 'Microsoft.Web/sites/basicPublishingCredentialsPolicies@2023-12-01' = {
  parent: site
  name: 'ftp'
  properties: { allow: false }
}

// ---------------------------------------------------------------- sign-in: an app registration in Entra ID
resource signIn 'Microsoft.Graph/applications@v1.0' = {
  uniqueName: '${prefix}-web-${unique}' // makes the deployment repeatable: the same registration is updated, not duplicated
  displayName: 'Sim Drop Lab (${site.name})'
  signInAudience: 'AzureADMyOrg' // only accounts in this directory can sign in
  web: {
    redirectUris: [ 'https://${site.properties.defaultHostName}/.auth/login/aad/callback' ]
    implicitGrantSettings: { enableIdTokenIssuance: true } // sign-in only needs an ID token, so no client secret exists
  }
}

resource signInPrincipal 'Microsoft.Graph/servicePrincipals@v1.0' = {
  appId: signIn.appId
}

resource auth 'Microsoft.Web/sites/config@2023-12-01' = {
  parent: site
  name: 'authsettingsV2'
  dependsOn: [ signInPrincipal ]
  properties: {
    platform: { enabled: true }
    globalValidation: {
      requireAuthentication: true // nobody reaches the app without signing in
      unauthenticatedClientAction: 'RedirectToLoginPage'
      redirectToProvider: 'azureactivedirectory'
    }
    identityProviders: {
      azureActiveDirectory: {
        enabled: true
        registration: {
          openIdIssuer: '${environment().authentication.loginEndpoint}${tenant().tenantId}/v2.0'
          clientId: signIn.appId
        }
        validation: { allowedAudiences: [ signIn.appId ] }
      }
    }
    login: { tokenStore: { enabled: false } } // the app only needs to know who you are, not to call anything as you
    httpSettings: { requireHttps: true }
  }
}

// ---------------------------------------------------------------- what the web app's identity may do (least privilege)
resource canUseResults 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: results // this one container, not the whole account
  name: guid(results.id, site.id, roles.blobDataContributor)
  properties: {
    principalId: site.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.blobDataContributor)
  }
}

resource canSignLinks 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: storage // asking for a user delegation key is an account-level action; it grants no data access by itself
  name: guid(storage.id, site.id, roles.blobDelegator)
  properties: {
    principalId: site.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.blobDelegator)
  }
}

resource canSend 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: queue // send to this queue only
  name: guid(queue.id, site.id, roles.busDataSender)
  properties: {
    principalId: site.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.busDataSender)
  }
}

// ---------------------------------------------------------------- spending alarm
resource budget 'Microsoft.Consumption/budgets@2023-11-01' = if (budgetUsd > 0 && !empty(budgetEmail) && !empty(budgetStart)) {
  name: '${prefix}-budget'
  properties: {
    category: 'Cost'
    amount: budgetUsd
    timeGrain: 'Monthly'
    timePeriod: { startDate: budgetStart }
    notifications: {
      half: { enabled: true, operator: 'GreaterThanOrEqualTo', threshold: 50, contactEmails: [ budgetEmail ], thresholdType: 'Actual' }
      most: { enabled: true, operator: 'GreaterThanOrEqualTo', threshold: 80, contactEmails: [ budgetEmail ], thresholdType: 'Actual' }
      all: { enabled: true, operator: 'GreaterThanOrEqualTo', threshold: 100, contactEmails: [ budgetEmail ], thresholdType: 'Forecasted' }
    }
  }
}

output webName string = site.name
output webUrl string = 'https://${site.properties.defaultHostName}'
output signInAppId string = signIn.appId
output storageName string = storage.name
output busName string = bus.name
output queueName string = queue.name
output insightsName string = insights.name
output insightsConnection string = insights.properties.ConnectionString // an ingestion address, not a key to read anything
