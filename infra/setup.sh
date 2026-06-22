#!/usr/bin/env bash
# One-time GCP infrastructure setup for outdoor-elements.
# Run once from a machine with gcloud authenticated as project owner.
# Safe to re-run — most commands use "|| true" to skip already-existing resources.
#
# After this script:
#   1. Add your secrets (GEMINI_API_KEY, OE_PASSCODE) — see the end of this file.
#   2. Build and deploy: gcloud builds submit --project=$PROJECT_ID
#      OR: push to main and Cloud Build triggers automatically.
#
# Estimated monthly cost (low-traffic):
#   Cloud Run (1 min instance, 1 vCPU, 2 GiB)   ~$20
#   Cloud SQL db-f1-micro + 10 GB SSD            ~$10
#   Cloud Storage (jobs bucket, ~10 GB)           ~$0.20
#   Artifact Registry                             ~$0.50
#   Secret Manager                                ~$0.10
#   Total                                        ~$31 / month

set -euo pipefail

PROJECT_ID="outdoor-elements-499605"
REGION="us-central1"
AR_REPO="oe"
DB_INSTANCE="oe-db"
DB_NAME="outdoor_elements"
DB_USER="oe"
JOBS_BUCKET="${PROJECT_ID}-jobs"
SERVICE_NAME="oe-app"
SA_NAME="oe-app-sa"
IMAGE="us-central1-docker.pkg.dev/${PROJECT_ID}/${AR_REPO}/app:latest"

echo "=== [1/10] Setting project ==="
gcloud config set project "$PROJECT_ID"

echo "=== [2/10] Enabling APIs ==="
gcloud services enable \
  run.googleapis.com \
  sqladmin.googleapis.com \
  secretmanager.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  storage.googleapis.com \
  --project="$PROJECT_ID"

echo "=== [3/10] Creating Artifact Registry repo ==="
gcloud artifacts repositories create "$AR_REPO" \
  --repository-format=docker \
  --location="$REGION" \
  --description="Outdoor Elements app images" \
  --project="$PROJECT_ID" || true

echo "=== [4/10] Creating Cloud SQL instance (takes ~5 min) ==="
# db-g1-small = cheapest shared-core Enterprise tier (~$8/month in us-central1)
if gcloud sql instances describe "$DB_INSTANCE" --project="$PROJECT_ID" &>/dev/null; then
  echo "Instance $DB_INSTANCE already exists, skipping."
else
  gcloud sql instances create "$DB_INSTANCE" \
    --database-version=POSTGRES_16 \
    --edition=ENTERPRISE \
    --tier=db-g1-small \
    --region="$REGION" \
    --storage-type=SSD \
    --storage-size=10 \
    --storage-auto-increase \
    --no-backup \
    --project="$PROJECT_ID"
fi

echo "=== [5/10] Creating database and user ==="
gcloud sql databases create "$DB_NAME" \
  --instance="$DB_INSTANCE" \
  --project="$PROJECT_ID" || true

# Generate a strong random password
DB_PASSWORD=$(openssl rand -hex 20)
gcloud sql users create "$DB_USER" \
  --instance="$DB_INSTANCE" \
  --password="$DB_PASSWORD" \
  --project="$PROJECT_ID" || {
  echo "User already exists — resetting password..."
  gcloud sql users set-password "$DB_USER" \
    --instance="$DB_INSTANCE" \
    --password="$DB_PASSWORD" \
    --project="$PROJECT_ID"
}

INSTANCE_CONN="${PROJECT_ID}:${REGION}:${DB_INSTANCE}"
DATABASE_URL="postgresql+psycopg2://${DB_USER}:${DB_PASSWORD}@/${DB_NAME}?host=/cloudsql/${INSTANCE_CONN}"

echo "=== [6/10] Storing secrets in Secret Manager ==="
# DATABASE_URL (auto-generated above)
printf '%s' "$DATABASE_URL" | gcloud secrets create DATABASE_URL \
  --data-file=- --project="$PROJECT_ID" 2>/dev/null || \
printf '%s' "$DATABASE_URL" | gcloud secrets versions add DATABASE_URL \
  --data-file=- --project="$PROJECT_ID"

# GEMINI_API_KEY — read from the repo's .env file (never committed to git)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/../.env"
if [[ -f "$ENV_FILE" ]]; then
  GEMINI_KEY=$(grep -E '^GEMINI_API_KEY=' "$ENV_FILE" | cut -d= -f2- | tr -d '[:space:]')
  if [[ -n "$GEMINI_KEY" ]]; then
    printf '%s' "$GEMINI_KEY" | gcloud secrets create GEMINI_API_KEY \
      --data-file=- --project="$PROJECT_ID" 2>/dev/null || \
    printf '%s' "$GEMINI_KEY" | gcloud secrets versions add GEMINI_API_KEY \
      --data-file=- --project="$PROJECT_ID"
    echo "GEMINI_API_KEY stored from .env"
  else
    echo "WARNING: GEMINI_API_KEY not found in $ENV_FILE — add it manually (see end of script)"
  fi
else
  echo "WARNING: .env not found at $ENV_FILE — add GEMINI_API_KEY manually (see end of script)"
fi

# OE_PASSCODE — generate a strong random secret (replaces the hardcoded default "2811")
OE_PASSCODE=$(openssl rand -hex 24)
printf '%s' "$OE_PASSCODE" | gcloud secrets create OE_PASSCODE \
  --data-file=- --project="$PROJECT_ID" 2>/dev/null || \
printf '%s' "$OE_PASSCODE" | gcloud secrets versions add OE_PASSCODE \
  --data-file=- --project="$PROJECT_ID"
echo "OE_PASSCODE generated and stored in Secret Manager (retrieve with: gcloud secrets versions access latest --secret=OE_PASSCODE)"

echo "=== [7/10] Creating Cloud Storage bucket for job artifacts ==="
gcloud storage buckets create "gs://${JOBS_BUCKET}" \
  --location="$REGION" \
  --default-storage-class=STANDARD \
  --project="$PROJECT_ID" || true

echo "=== [8/10] Creating service account ==="
gcloud iam service-accounts create "$SA_NAME" \
  --description="Outdoor Elements Cloud Run runtime SA" \
  --display-name="OE App" \
  --project="$PROJECT_ID" || true

SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

echo "=== [9/10] Granting IAM roles to service account ==="
for ROLE in \
  roles/cloudsql.client \
  roles/secretmanager.secretAccessor \
  roles/logging.logWriter \
  roles/monitoring.metricWriter; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="$ROLE" \
    --quiet
done

# Storage access scoped to the jobs bucket only
gcloud storage buckets add-iam-policy-binding "gs://${JOBS_BUCKET}" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role=roles/storage.objectAdmin

# Allow Cloud Build to deploy Cloud Run services
CB_SA="${PROJECT_ID}@cloudbuild.gserviceaccount.com"
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${CB_SA}" \
  --role=roles/run.admin \
  --quiet
gcloud iam service-accounts add-iam-policy-binding "$SA_EMAIL" \
  --member="serviceAccount:${CB_SA}" \
  --role=roles/iam.serviceAccountUser \
  --project="$PROJECT_ID"

echo "=== [10/10] Creating Cloud Run service ==="
# Use the public hello image as a placeholder; Cloud Build replaces it with the real image.
PLACEHOLDER="us-docker.pkg.dev/cloudrun/container/hello:latest"
gcloud run deploy "$SERVICE_NAME" \
  --image="$PLACEHOLDER" \
  --region="$REGION" \
  --platform=managed \
  --service-account="$SA_EMAIL" \
  --add-cloudsql-instances="$INSTANCE_CONN" \
  --set-secrets="DATABASE_URL=DATABASE_URL:latest,GEMINI_API_KEY=GEMINI_API_KEY:latest,OE_PASSCODE=OE_PASSCODE:latest" \
  --memory=2Gi \
  --cpu=1 \
  --min-instances=1 \
  --max-instances=3 \
  --timeout=3600 \
  --add-volume="name=jobs,type=cloud-storage,bucket=${JOBS_BUCKET}" \
  --add-volume-mount="volume=jobs,mount-path=/app/backend/jobs" \
  --execution-environment=gen2 \
  --allow-unauthenticated \
  --project="$PROJECT_ID" \
  --quiet

echo ""
echo "================================================================"
echo " NEXT STEPS"
echo "================================================================"
echo ""
echo "1. Build and push the application image:"
echo "   gcloud builds submit --project=$PROJECT_ID"
echo ""
echo "2. Get your service URL once deployed:"
echo "   gcloud run services describe $SERVICE_NAME \\"
echo "     --region=$REGION --format='value(status.url)'"
echo ""
echo "DB password auto-generated → Secret Manager (DATABASE_URL)."
echo "GEMINI_API_KEY read from .env → Secret Manager."
echo "OE_PASSCODE auto-generated → Secret Manager. Retrieve with:"
echo "   gcloud secrets versions access latest --secret=OE_PASSCODE --project=$PROJECT_ID"
