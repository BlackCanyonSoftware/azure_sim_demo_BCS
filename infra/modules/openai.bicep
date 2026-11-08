// Azure OpenAI with one small model, pay per use (GlobalStandard), no keys: callers sign in with Entra ID and need the OpenAI User role.
// The lab allows only small models at up to 10 (thousand tokens a minute); a demo answer costs a fraction of a cent.

@description('Account name, also its address NAME.openai.azure.com: 2-64 letters, digits and hyphens, unique in Azure')
param name string
param location string = resourceGroup().location

@allowed([ 'gpt-4.1-mini', 'gpt-4.1-nano', 'gpt-4o-mini', 'gpt-5-mini', 'gpt-5-nano' ])
param model string = 'gpt-4.1-mini'

@description('Rate limit, in thousands of tokens a minute (the lab allows 1-10)')
@minValue(1)
@maxValue(10)
param capacity int = 5

@description('Object ids of identities that call the model (the web app, and Can-AI for local runs)')
param users array = []

var versions = {
  'gpt-4.1-mini': '2025-04-14'
  'gpt-4.1-nano': '2025-04-14'
  'gpt-4o-mini': '2024-07-18'
  'gpt-5-mini': '2025-08-07'
  'gpt-5-nano': '2025-08-07'
}
var openAiUser = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'

resource ai 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: name
  location: location
  kind: 'OpenAI'
  sku: { name: 'S0' }
  properties: {
    customSubDomainName: name
    disableLocalAuth: true // no API keys
    publicNetworkAccess: 'Enabled'
  }
}

resource deployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: ai
  name: model
  sku: { name: 'GlobalStandard', capacity: capacity }
  properties: {
    model: { format: 'OpenAI', name: model, version: versions[model] }
    versionUpgradeOption: 'OnceNewDefaultVersionAvailable'
  }
}

resource canCall 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for id in users: {
  scope: ai
  name: guid(ai.id, id, openAiUser)
  properties: {
    principalId: id
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', openAiUser)
  }
}]

output name string = ai.name
output endpoint string = ai.properties.endpoint
output deploymentName string = deployment.name
