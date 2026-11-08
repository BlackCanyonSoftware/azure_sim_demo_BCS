// Monitoring: a Log Analytics workspace with a small daily cap (the lab requires one), and Application Insights writing into it.
// Requests, errors and log lines from the web app land here; the azure tool's inspect action reads them.

@description('Base name; the two resources are NAME-logs and NAME-insights')
param name string
param location string = resourceGroup().location

@description('Most data the workspace takes in a day, in GB (the lab allows 0.05, 0.1, 0.2, 0.5 or 1). 0.1 GB is plenty for a demo and costs at most about $0.25 a day.')
param dailyCapGb string = '0.1'

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${name}-logs'
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
    workspaceCapping: { dailyQuotaGb: json(dailyCapGb) }
  }
}

resource insights 'Microsoft.Insights/components@2020-02-02' = {
  name: '${name}-insights'
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logs.id
  }
}

output insightsName string = insights.name
output workspaceName string = logs.name
@description('Goes in the web app setting APPLICATIONINSIGHTS_CONNECTION_STRING (it identifies where telemetry goes; it is not a password)')
output connectionString string = insights.properties.ConnectionString
