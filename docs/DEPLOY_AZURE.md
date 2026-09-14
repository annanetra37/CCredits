# Deploying to Azure

One container, one Postgres, one storage account. Nothing runs on a schedule,
so there is no worker, no function app and no queue.

| Piece | Azure service | Why this one |
|---|---|---|
| The app | **Azure Container Apps** | Runs the Dockerfile as-is, scales to zero between demos, managed identity built in |
| The database | **Azure Database for PostgreSQL — Flexible Server** | The app is plain Postgres 16; Burstable B1ms is enough for the pilot |
| Uploaded originals | **Azure Blob Storage** | The container filesystem is ephemeral; the original files are the evidence |
| Secrets | **Container Apps secrets**, or Key Vault | Managed identity means the storage key never exists |

Container Apps is the recommendation. App Service for Containers also works and
is noted at the end.

---

## 1. Variables

```bash
RG=ccredits-rg
LOC=westeurope            # or northeurope — keep EU for EU data
ACR=ccreditsacr$RANDOM    # registry names are globally unique
PG=ccredits-pg-$RANDOM
SA=ccreditssa$RANDOM      # storage account: lowercase letters and digits only
APP=ccredits
ENVNAME=ccredits-env
PGUSER=ccadmin
PGPASS='<a strong password>'
```

## 2. Resource group

```bash
az group create --name $RG --location $LOC
```

## 3. Postgres

```bash
az postgres flexible-server create \
  --resource-group $RG --name $PG --location $LOC \
  --tier Burstable --sku-name Standard_B1ms \
  --storage-size 32 --version 16 \
  --admin-user $PGUSER --admin-password "$PGPASS" \
  --public-access 0.0.0.0    # allow Azure services; tighten later

az postgres flexible-server db create \
  --resource-group $RG --server-name $PG --database-name ccredits
```

The connection string needs TLS — Azure Postgres refuses plaintext:

```
postgresql://USER:PASS@HOST.postgres.database.azure.com:5432/ccredits?sslmode=require
```

The app normalises `postgres://` to `postgresql://` itself, so either prefix is fine.

## 4. Storage account and container

```bash
az storage account create \
  --resource-group $RG --name $SA --location $LOC \
  --sku Standard_LRS --kind StorageV2 --min-tls-version TLS1_2

az storage container create --account-name $SA --name uploads
```

## 5. Build the image

```bash
az acr create --resource-group $RG --name $ACR --sku Basic --admin-enabled true
az acr build --registry $ACR --image ccredits:latest .
```

`az acr build` builds in Azure, so no local Docker daemon is needed.

## 6. Deploy the container app

```bash
az containerapp env create --resource-group $RG --name $ENVNAME --location $LOC

az containerapp create \
  --resource-group $RG --name $APP --environment $ENVNAME \
  --image $ACR.azurecr.io/ccredits:latest \
  --registry-server $ACR.azurecr.io \
  --target-port 8000 --ingress external \
  --min-replicas 1 --max-replicas 2 \
  --system-assigned \
  --secrets "db-url=postgresql://$PGUSER:$PGPASS@$PG.postgres.database.azure.com:5432/ccredits?sslmode=require" \
            "admin-token=$(openssl rand -hex 24)" \
  --env-vars \
      DATABASE_URL=secretref:db-url \
      ADMIN_TOKEN=secretref:admin-token \
      APP_ENV=production \
      STORAGE_BACKEND=azure \
      AZURE_STORAGE_ACCOUNT=$SA \
      AZURE_STORAGE_CONTAINER=uploads
```

`--target-port 8000` matches the Dockerfile's default. Container Apps does not
inject `PORT`, so leave `PORT` unset and the container listens on 8000.

## 7. Let the app reach storage without a key

This is the part worth doing properly. Rather than putting a storage key in
configuration, give the container app's own identity permission to write blobs:

```bash
PRINCIPAL=$(az containerapp show -g $RG -n $APP --query identity.principalId -o tsv)
SCOPE=$(az storage account show -g $RG -n $SA --query id -o tsv)

az role assignment create \
  --assignee $PRINCIPAL \
  --role "Storage Blob Data Contributor" \
  --scope $SCOPE
```

With `AZURE_STORAGE_ACCOUNT` set and no connection string, the app authenticates
through `DefaultAzureCredential` and there is no storage secret anywhere.

If you would rather use a key, set `AZURE_STORAGE_CONNECTION_STRING` as a secret
instead and skip the role assignment.

## 8. Check it

```bash
FQDN=$(az containerapp show -g $RG -n $APP --query properties.configuration.ingress.fqdn -o tsv)
curl https://$FQDN/api/health
# {"status":"ok","database":true,"env":"production"}
```

The schema is applied on startup (`AUTO_MIGRATE=true`), so there is no
migration step. If health reports `degraded`, the message names the cause; it is
almost always `DATABASE_URL` or a firewall rule.

Open `https://$FQDN` and upload through the portal.

## 9. Updating

```bash
az acr build --registry $ACR --image ccredits:latest .
az containerapp update -g $RG -n $APP --image $ACR.azurecr.io/ccredits:latest
```

## Backups

Flexible Server takes automatic daily backups with point-in-time restore; set
the retention you want:

```bash
az postgres flexible-server update -g $RG -n $PG --backup-retention 14
```

Bronze is the only thing that matters — Silver and Gold are views and rebuild
themselves. If you also want a dump in the same storage account:

```bash
pg_dump "$DATABASE_URL" | gzip > ccredits-$(date +%F).sql.gz
az storage blob upload --account-name $SA --container-name uploads \
  --name backups/ccredits-$(date +%F).sql.gz --file ccredits-$(date +%F).sql.gz
```

---

## App Service for Containers instead

Works equally well; two differences matter:

- Set **`WEBSITES_PORT=8000`** so the platform knows which port to route to.
- Enable the system-assigned identity under Identity, then make the same
  `Storage Blob Data Contributor` assignment.

```bash
az webapp create -g $RG -p <plan> -n $APP --deployment-container-image-name $ACR.azurecr.io/ccredits:latest
az webapp config appsettings set -g $RG -n $APP --settings WEBSITES_PORT=8000 STORAGE_BACKEND=azure AZURE_STORAGE_ACCOUNT=$SA
```

## Cost, roughly

Burstable B1ms Postgres plus a Container App scaled to one small replica plus a
Standard_LRS storage account is a modest monthly figure for a pilot — the
database dominates it. Scale the container to zero between demos with
`--min-replicas 0` if the cold start is acceptable.

## What is deliberately not deployed

No worker, no scheduler, no cache. Everything recomputes on read, so there is
nothing to keep warm and nothing to invalidate.
