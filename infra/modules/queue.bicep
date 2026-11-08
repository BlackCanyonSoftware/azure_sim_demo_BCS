// A job queue: Service Bus Basic (pennies a month), no keys (senders and receivers sign in with Entra ID).
// The web app sends a job; a worker (on KPrime, with its GPU and tools) takes it, does the heavy part and stores the result.

@description('Namespace name: 6-50 letters, digits and hyphens, unique in Azure')
param name string
param location string = resourceGroup().location
param queueName string = 'jobs'

@description('Object ids of identities that send jobs (usually the web app)')
param senders array = []

@description('Object ids of identities that take jobs (usually the KPrime worker, which signs in as Can-AI\'s lab identity)')
param receivers array = []

var roles = {
  sender: '69a216fc-b8fb-44d8-bc22-1f3c2cd27a39'
  receiver: '4f6d3b9b-027b-4f4c-9142-0e5a2a2247e0'
}

resource bus 'Microsoft.ServiceBus/namespaces@2021-11-01' = {
  name: name
  location: location
  sku: { name: 'Basic', tier: 'Basic' }
  properties: {
    disableLocalAuth: true // no connection strings with keys (TLS 1.2 is Azure's minimum for new namespaces)
  }
}

resource queue 'Microsoft.ServiceBus/namespaces/queues@2021-11-01' = {
  parent: bus
  name: queueName
  properties: {
    lockDuration: 'PT5M' // a worker has five minutes before a job it took goes back on the queue
    maxDeliveryCount: 3
    defaultMessageTimeToLive: 'P1D'
  }
}

resource canSend 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for id in senders: {
  scope: queue
  name: guid(queue.id, id, roles.sender)
  properties: {
    principalId: id
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.sender)
  }
}]

resource canReceive 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for id in receivers: {
  scope: queue
  name: guid(queue.id, id, roles.receiver)
  properties: {
    principalId: id
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.receiver)
  }
}]

output name string = bus.name
output host string = '${bus.name}.servicebus.windows.net'
output queueName string = queue.name
