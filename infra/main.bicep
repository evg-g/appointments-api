// Azure Container Apps deployment for appointments-api.
//
// Why this exists: the delivery pipeline (cd.yml) targets Azure Container Apps as the primary
// environment. This template is the infrastructure-as-code for one environment (staging or
// production) — declarative, repeatable, reviewable in a PR. docker-compose.prod.yml is the
// no-cloud fallback; this is the real target.
//
// What it creates:
//   - a Log Analytics workspace (Container Apps needs one for logs),
//   - a Container Apps managed environment,
//   - an Azure Database for PostgreSQL flexible server + database,
//   - an Azure Cache for Redis,
//   - the API container app (external ingress, autoscaling, health probes),
//   - a migration Job (run once per deploy, before the app rolls — the "expand" step in cd.yml).
//
// Secrets (JWT signing key, DB password) are passed as secure parameters and stored as Container
// Apps secrets, never hardcoded. In cd.yml the pipeline authenticates with OIDC (no client secret)
// and the app pulls its image from GHCR.

@description('Deployment environment name, used as a suffix on resource names.')
@allowed(['staging', 'production'])
param environmentName string

@description('Azure region for all resources.')
param location string = resourceGroup().location

@description('Container image reference, e.g. ghcr.io/evg-g/appointments-api:1.2.3')
param image string

@description('PostgreSQL administrator login.')
param dbAdminUser string = 'aurora'

@description('PostgreSQL administrator password.')
@secure()
param dbAdminPassword string

@description('JWT signing secret (32+ bytes). Provide via a secure pipeline variable.')
@secure()
param jwtSecret string

@description('Min / max replicas for the API.')
param minReplicas int = 1
param maxReplicas int = 5

var suffix = uniqueString(resourceGroup().id, environmentName)
var dbName = 'aurora'

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-appts-${environmentName}'
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource caeEnv 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: 'cae-appts-${environmentName}'
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
  }
}

resource postgres 'Microsoft.DBforPostgreSQL/flexibleServers@2023-06-01-preview' = {
  name: 'psql-appts-${environmentName}-${suffix}'
  location: location
  sku: {
    name: 'Standard_B1ms'
    tier: 'Burstable'
  }
  properties: {
    version: '16'
    administratorLogin: dbAdminUser
    administratorLoginPassword: dbAdminPassword
    storage: { storageSizeGB: 32 }
    highAvailability: { mode: 'Disabled' }
    backup: { backupRetentionDays: 7 }
  }

  resource database 'databases@2023-06-01-preview' = {
    name: dbName
  }

  // Container Apps egress is not a fixed IP; allow Azure services to reach the DB. For production,
  // prefer VNet integration + private endpoint (documented in docs/DEPLOYMENT.md as the hardening step).
  resource allowAzure 'firewallRules@2023-06-01-preview' = {
    name: 'AllowAllAzureServices'
    properties: {
      startIpAddress: '0.0.0.0'
      endIpAddress: '0.0.0.0'
    }
  }
}

resource redis 'Microsoft.Cache/redis@2024-03-01' = {
  name: 'redis-appts-${environmentName}-${suffix}'
  location: location
  properties: {
    sku: {
      name: 'Basic'
      family: 'C'
      capacity: 0
    }
    enableNonSslPort: false
    minimumTlsVersion: '1.2'
  }
}

var databaseUrl = 'postgresql+psycopg://${dbAdminUser}:${dbAdminPassword}@${postgres.properties.fullyQualifiedDomainName}:5432/${dbName}?sslmode=require'
var redisUrl = 'rediss://:${redis.listKeys().primaryKey}@${redis.properties.hostName}:${redis.properties.sslPort}/0'

resource api 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'appointments-api-${environmentName}'
  location: location
  properties: {
    managedEnvironmentId: caeEnv.id
    configuration: {
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
      }
      secrets: [
        { name: 'database-url', value: databaseUrl }
        { name: 'redis-url', value: redisUrl }
        { name: 'jwt-secret', value: jwtSecret }
      ]
    }
    template: {
      containers: [
        {
          name: 'api'
          image: image
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            { name: 'APP_ENV', value: environmentName }
            { name: 'DATABASE_URL', secretRef: 'database-url' }
            { name: 'REDIS_URL', secretRef: 'redis-url' }
            { name: 'JWT_SECRET', secretRef: 'jwt-secret' }
          ]
          probes: [
            {
              type: 'Liveness'
              httpGet: { path: '/health/live', port: 8000 }
              periodSeconds: 30
            }
            {
              type: 'Readiness'
              httpGet: { path: '/health/ready', port: 8000 }
              periodSeconds: 10
            }
          ]
        }
      ]
      scale: {
        minReplicas: minReplicas
        maxReplicas: maxReplicas
        rules: [
          {
            name: 'http-scale'
            http: { metadata: { concurrentRequests: '50' } }
          }
        ]
      }
    }
  }
}

// Migration job: cd.yml starts this before rolling the app (the expand phase). It runs
// `alembic upgrade head` against the same DB using the same image, then exits.
resource migrateJob 'Microsoft.App/jobs@2024-03-01' = {
  name: 'migrate-${environmentName}'
  location: location
  properties: {
    environmentId: caeEnv.id
    configuration: {
      triggerType: 'Manual'
      replicaTimeout: 600
      replicaRetryLimit: 1
      manualTriggerConfig: {
        parallelism: 1
        replicaCompletionCount: 1
      }
      secrets: [
        { name: 'database-url', value: databaseUrl }
      ]
    }
    template: {
      containers: [
        {
          name: 'migrate'
          image: image
          command: ['alembic']
          args: ['upgrade', 'head']
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            { name: 'DATABASE_URL', secretRef: 'database-url' }
          ]
        }
      ]
    }
  }
}

output apiFqdn string = api.properties.configuration.ingress.fqdn
output apiUrl string = 'https://${api.properties.configuration.ingress.fqdn}'
