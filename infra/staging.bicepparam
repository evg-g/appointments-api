using './main.bicep'

// Example parameter file for the staging environment.
// Secrets are read from the environment at deploy time — nothing sensitive is committed.
// The pipeline sets DB_ADMIN_PASSWORD / JWT_SECRET; a human deploying locally exports them first.

param environmentName = 'staging'
param image = readEnvironmentVariable('IMAGE', 'ghcr.io/evg-g/appointments-api:latest')
param dbAdminPassword = readEnvironmentVariable('DB_ADMIN_PASSWORD')
param jwtSecret = readEnvironmentVariable('JWT_SECRET')
param minReplicas = 1
param maxReplicas = 3
