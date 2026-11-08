// Storage for results: one private container, no account keys at all (everything signs in with Entra ID), old files deleted automatically.
// Writers can read and write that container; signers may also make short-lived read links (user delegation SAS) so a browser can show a file.

@description('Storage account name: 3-24 lowercase letters and digits, unique in Azure')
@minLength(3)
@maxLength(24)
param name string
param location string = resourceGroup().location
param containerName string = 'results'

@description('Files older than this many days are deleted')
param keepDays int = 7

@description('Object ids of identities that read and write the container (web app, worker, Can-AI for local runs)')
param writers array = []

@description('Object ids of identities that may sign read links (usually the web app)')
param signers array = []

var roles = {
  blobDataContributor: 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
  blobDelegator: 'db58b8e5-c6ad-4a2a-8342-4190687cbf4a'
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: name
  location: location
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  properties: {
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false // no account keys: every reader and writer signs in
    defaultToOAuthAuthentication: true
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
  }
}

resource blobs 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource container 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobs
  name: containerName
  properties: { publicAccess: 'None' }
}

resource tidy 'Microsoft.Storage/storageAccounts/managementPolicies@2023-05-01' = {
  parent: storage
  name: 'default'
  properties: {
    policy: {
      rules: [
        {
          name: 'delete-old-results'
          enabled: true
          type: 'Lifecycle'
          definition: {
            filters: { blobTypes: [ 'blockBlob' ], prefixMatch: [ '${containerName}/' ] }
            actions: { baseBlob: { delete: { daysAfterModificationGreaterThan: keepDays } } }
          }
        }
      ]
    }
  }
}

resource canWrite 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for id in writers: {
  scope: container // this one container, not the whole account
  name: guid(container.id, id, roles.blobDataContributor)
  properties: {
    principalId: id
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.blobDataContributor)
  }
}]

resource canSign 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for id in signers: {
  scope: storage // asking for a delegation key is an account-level action; it grants no data access by itself
  name: guid(storage.id, id, roles.blobDelegator)
  properties: {
    principalId: id
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.blobDelegator)
  }
}]

output name string = storage.name
output accountUrl string = storage.properties.primaryEndpoints.blob
output containerName string = container.name
