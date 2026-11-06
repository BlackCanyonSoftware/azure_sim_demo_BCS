// Azure side of the sim demo: everything in one resource group, rebuilt or removed with one command.
//
//   browser ──► Static Web App (page + Python API, sign-in, "operator" role only)
//                   │ send-only key                      ▲ short read links
//                   ▼                                    │
//              Service Bus queue "sim-jobs" ──► KPrime worker (listen-only key, outgoing only)
//                                                    │ write-only container link
//                                                    ▼
//                                     Storage: container "results" (status, GIF, MP4, last frame)
//   Application Insights collects the API's and the worker's logs and traces.

@description('Short name put in front of every resource name.')
@minLength(3)
@maxLength(10)
param prefix string = 'simdemo'

@description('Where the queue, storage and monitoring live.')
param location string = resourceGroup().location

@description('Static Web Apps run in a few regions only: westus2, centralus, eastus2, westeurope, eastasia.')
param webLocation string = 'eastus2'

@description('Monthly budget in US dollars; an email goes out at 50%, 80% and 100% of it. 0 = no budget.')
param budgetUsd int = 5

@description('Who the budget emails go to (given at deploy time, never stored in the repo).')
param budgetEmail string = ''

@description('First day of the current month, yyyy-MM-01, for the budget.')
param budgetStart string = ''

var unique = uniqueString(resourceGroup().id)
var tags = { project: 'azure-sim-demo' }

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

resource sendRule 'Microsoft.ServiceBus/namespaces/queues/authorizationRules@2022-10-01-preview' = {
  parent: queue
  name: 'web-send'
  properties: { rights: [ 'Send' ] }
}

resource listenRule 'Microsoft.ServiceBus/namespaces/queues/authorizationRules@2022-10-01-preview' = {
  parent: queue
  name: 'worker-listen'
  properties: { rights: [ 'Listen' ] }
}

// ---------------------------------------------------------------- the page and its API
resource web 'Microsoft.Web/staticSites@2023-12-01' = {
  name: '${prefix}-web'
  location: webLocation
  tags: tags
  sku: { name: 'Free', tier: 'Free' }
  properties: {}
}

resource webSettings 'Microsoft.Web/staticSites/config@2023-12-01' = {
  parent: web
  name: 'appsettings'
  properties: {
    JOBS_QUEUE: queue.name
    JOBS_SEND: sendRule.listKeys().primaryConnectionString
    RESULTS_STORAGE: 'DefaultEndpointsProtocol=https;AccountName=${storage.name};AccountKey=${storage.listKeys().keys[0].value};EndpointSuffix=${environment().suffixes.storage}'
    RESULTS_CONTAINER: results.name
    APPLICATIONINSIGHTS_CONNECTION_STRING: insights.properties.ConnectionString
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

output webName string = web.name
output webUrl string = 'https://${web.properties.defaultHostname}'
output storageName string = storage.name
output busName string = bus.name
output queueName string = queue.name
output insightsName string = insights.name
output insightsConnection string = insights.properties.ConnectionString // an ingestion address, not a key to read anything
