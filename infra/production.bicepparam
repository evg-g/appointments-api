using './main.bicep'

// Example parameter file for the production environment. Secrets come from the environment.

param environmentName = 'production'
param image = readEnvironmentVariable('IMAGE', 'ghcr.io/evg-g/appointments-api:latest')
param dbAdminPassword = readEnvironmentVariable('DB_ADMIN_PASSWORD')
param jwtSecret = readEnvironmentVariable('JWT_SECRET')
param minReplicas = 2
param maxReplicas = 8
