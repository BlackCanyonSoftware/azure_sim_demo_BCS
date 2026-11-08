// The starter: an internal web app with AI in it (Azure OpenAI), heavy work done by a worker on KPrime through a queue, results in storage,
// and monitoring. Staff sign in with Entra ID; every service is reached with an identity, never a key. Change what you need; keep the shape.
//
//   Staff --HTTPS, Entra sign-in--> web app --calls--> Azure OpenAI
//                                     |  \--sends jobs--> Service Bus queue --takes jobs--> KPrime worker --writes--> Storage
//                                     \--reads results, signs read links--------------------------------------------> Storage
//   web app --telemetry--> Application Insights --> Log Analytics

targetScope = 'resourceGroup'

@description('Short name for this demo: lowercase letters and digits, used in every resource name')
@minLength(3)
@maxLength(10)
param prefix string = 'studio'
param location string = resourceGroup().location

@description('The Entra sign-in app id from the owner (canai-azure-lab signin-app NAME.azurewebsites.net). Empty: no sign-in yet.')
param signInAppId string = ''

@description('Entra object ids of the people allowed to use the API (the owner\'s comes with the sign-in app). Empty: everyone who can sign in.')
param operators array = []

@description('Can-AI\'s lab identity (the azure tool\'s status gives it): the KPrime worker signs in as it, and so does the app when it runs on KPrime for recordings')
param labPrincipalId string = ''

@allowed([ 'gpt-4.1-mini', 'gpt-4.1-nano', 'gpt-4o-mini', 'gpt-5-mini', 'gpt-5-nano' ])
param model string = 'gpt-4.1-mini'

// Names are fixed by the prefix and the lab, so a preview already shows the web app's address (the owner needs it for the sign-in app).
var suffix = take(uniqueString(resourceGroup().id, prefix), 6)
var names = {
  base: '${prefix}-${suffix}'
  web: '${prefix}-web-${suffix}'
  ai: '${prefix}-ai-${suffix}'
  bus: '${prefix}-bus-${suffix}'
  storage: take('${prefix}${suffix}files', 24)
}
var lab = empty(labPrincipalId) ? [] : [ labPrincipalId ]

module monitor 'modules/monitor.bicep' = {
  name: 'monitor'
  params: { name: names.base, location: location }
}

module web 'modules/web.bicep' = {
  name: 'web'
  params: {
    name: names.web
    location: location
    signInAppId: signInAppId
    insightsConnection: monitor.outputs.connectionString
    appSettings: {
      AI_ENDPOINT: 'https://${names.ai}.openai.azure.com/'
      AI_DEPLOYMENT: model
      JOBS_NAMESPACE: '${names.bus}.servicebus.windows.net'
      JOBS_QUEUE: 'jobs'
      RESULTS_ACCOUNT_URL: 'https://${names.storage}.blob.${environment().suffixes.storage}/'
      RESULTS_CONTAINER: 'results'
      OPERATORS: join(operators, ',')
      JOB_KINDS: 'echo, massing'  // "massing" is the KPrime worker's job; the handler in worker/handlers.py must know every kind listed here
      AI_MAX_TOKENS: '2000'  // the design brief (spaces + notes + 2-3 options with coordinates) is a much bigger answer than the starter's default 900; local thinking models need even more (run.sh uses 6000 for the KPrime model)
    }
  }
}

module ai 'modules/openai.bicep' = {
  name: 'ai'
  params: { name: names.ai, location: location, model: model, users: concat([ web.outputs.principalId ], lab) }
}

module queue 'modules/queue.bicep' = {
  name: 'queue'
  params: { name: names.bus, location: location, senders: concat([ web.outputs.principalId ], lab), receivers: lab }
}

module files 'modules/storage.bicep' = {
  name: 'files'
  params: { name: names.storage, location: location, writers: concat([ web.outputs.principalId ], lab), signers: concat([ web.outputs.principalId ], lab) }
}

output webName string = web.outputs.name
output webUrl string = web.outputs.url
output webHostname string = web.outputs.hostname
output signInRequired bool = web.outputs.signInRequired
output aiEndpoint string = ai.outputs.endpoint
output aiDeployment string = ai.outputs.deploymentName
output jobsNamespace string = queue.outputs.host
output jobsQueue string = queue.outputs.queueName
output resultsAccountUrl string = files.outputs.accountUrl
output resultsContainer string = files.outputs.containerName
output insightsName string = monitor.outputs.insightsName
