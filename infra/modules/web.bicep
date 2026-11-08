// The web app: App Service free tier (F1, Linux, Python 3.12) with its own managed identity (it signs in to the other services with it; no
// passwords in settings), HTTPS only, no FTP or deployment passwords, and staff sign-in with Entra ID in front of it when signInAppId is given.

@description('Web app name; its address is NAME.azurewebsites.net (2-60 letters, digits and hyphens, unique in Azure)')
param name string
param location string = resourceGroup().location

@description('Settings the app reads as environment variables (names and addresses only: nothing secret goes here)')
param appSettings object = {}

@description('The Entra sign-in app id the owner made with canai-azure-lab signin-app NAME.azurewebsites.net. Empty: NO sign-in, anyone with the address can use it; only for a first look.')
param signInAppId string = ''

@description('APPLICATIONINSIGHTS_CONNECTION_STRING from the monitor module (empty: no telemetry)')
param insightsConnection string = ''

param startupCommand string = 'gunicorn --bind=0.0.0.0 --timeout 120 --workers 2 app:app'

resource plan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: '${name}-plan'
  location: location
  kind: 'linux'
  sku: { name: 'F1' } // free: 60 CPU minutes a day, sleeps when idle (the first request after a while is slow)
  properties: { reserved: true }
}

var settings = union({
  SCM_DO_BUILD_DURING_DEPLOYMENT: 'true' // installs requirements.txt when the code is published
}, empty(insightsConnection) ? {} : { APPLICATIONINSIGHTS_CONNECTION_STRING: insightsConnection }, appSettings)

resource site 'Microsoft.Web/sites@2023-12-01' = {
  name: name
  location: location
  kind: 'app,linux'
  identity: { type: 'SystemAssigned' }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.12'
      appCommandLine: startupCommand
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      alwaysOn: false // not available on the free tier
      appSettings: [for s in items(settings): { name: s.key, value: string(s.value) }]
    }
  }
}

resource noScmPassword 'Microsoft.Web/sites/basicPublishingCredentialsPolicies@2023-12-01' = {
  parent: site
  name: 'scm'
  properties: { allow: false } // code is published with Entra ID (the azure tool's publish), never a password
}

resource noFtpPassword 'Microsoft.Web/sites/basicPublishingCredentialsPolicies@2023-12-01' = {
  parent: site
  name: 'ftp'
  properties: { allow: false }
}

resource signIn 'Microsoft.Web/sites/config@2023-12-01' = if (!empty(signInAppId)) {
  parent: site
  name: 'authsettingsV2'
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
          clientId: signInAppId
        }
        validation: { allowedAudiences: [ signInAppId ] }
      }
    }
    login: { tokenStore: { enabled: false } } // the app only needs to know who you are (X-MS-CLIENT-PRINCIPAL-* headers)
    httpSettings: { requireHttps: true }
  }
}

output name string = site.name
output url string = 'https://${site.properties.defaultHostName}'
output hostname string = site.properties.defaultHostName
output principalId string = site.identity.principalId
output signInRequired bool = !empty(signInAppId)
