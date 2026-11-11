# Goalvera preview environment

The hosted preview lives in its own Google Cloud project (`<your-project>`,
project number `<project-number>`), region `us-east1`. It has its own CMEK
recordings bucket, Identity Platform user pool, Artifact Registry repo, and
Cloud Run services. Data is fictional only (Everyday Heroes sample roster and
`staff.example` accounts). Nothing is copied from any other project.

Each merge to `main` runs `.github/workflows/deploy-preview.yml`: GitHub Actions
authenticates as a deployer service account through Workload Identity
Federation (GitHub environment `preview`, `main` only), builds one image per service (`review`, `upload-api`, `pipeline`),
and deploys those **digests** to preview. Cloud Run min instances are 0. Every
service, build, and Eventarc trigger uses one of the seven existing service
accounts; the default compute SA is never used.

The public landing site in `goalvera-app` is not part of this environment and
must not host the app backend.

The workflow reads four GitHub Actions variables (Settings → Secrets and
variables → Actions → Variables). If `GCP_PROJECT_ID` is not set (for example on
a fork), the deploy job is skipped.

| Variable | Example |
|---|---|
| `GCP_PROJECT_ID` | `<your-project>` (must end in `-preview`) |
| `GCP_PROJECT_NUMBER` | `<project-number>` |
| `GCP_WIF_PROVIDER` | `projects/<project-number>/locations/global/workloadIdentityPools/<pool>/providers/<provider>` |
| `GCP_DEPLOYER_SA` | `<deployer>@<your-project>.iam.gserviceaccount.com` |

## URLs

Cloud Run prints the service URLs at the end of each deploy
(`https://<service>-<project-number>.us-east1.run.app`):

- Review (min 0, runtime SA `review-web@`)
- Upload API (min 0, runtime SA `upload-api@`)
- Pipeline (private, runtime SA `pipeline@`)

## Promote and rollback

Out of scope for now. This repository does not include promote or rollback
workflows yet, and no image is copied outside the preview project.

## One-time setup (project owner)

1. **GitHub environment `preview`** (repo admin):
   Settings → Environments → New environment → name it `preview`, and limit
   deployment branches to `main`. The WIF binding only accepts tokens from this
   environment, so the workflow cannot deploy without it.
2. **Enable Identity Platform.** Turn on email/password, turn self sign-up
   off, and store the restricted browser API key in Secret Manager as
   `review-web-api-key`. The review service deploys once that secret exists.
3. **Project-level IAM** (needs a project owner). Run as a project owner:

```zsh
P=<your-project>
N=<project-number>
gcloud projects add-iam-policy-binding ${P} --member=serviceAccount:pipeline@${P}.iam.gserviceaccount.com --role=roles/speech.client --condition=None
gcloud projects add-iam-policy-binding ${P} --member=serviceAccount:pipeline@${P}.iam.gserviceaccount.com --role=roles/aiplatform.user --condition=None
gcloud projects add-iam-policy-binding ${P} --member=serviceAccount:pipeline-trigger@${P}.iam.gserviceaccount.com --role=roles/eventarc.eventReceiver --condition=None
gcloud projects add-iam-policy-binding ${P} --member=serviceAccount:service-${N}@gs-project-accounts.iam.gserviceaccount.com --role=roles/pubsub.publisher --condition=None
gcloud projects add-iam-policy-binding ${P} --member=serviceAccount:service-${N}@gcp-sa-eventarc.iam.gserviceaccount.com --role=roles/eventarc.serviceAgent --condition=None
gcloud projects add-iam-policy-binding ${P} --member=serviceAccount:builder@${P}.iam.gserviceaccount.com --role=roles/run.builder --condition=None
gcloud projects add-iam-policy-binding ${P} --member=serviceAccount:builder@${P}.iam.gserviceaccount.com --role=roles/logging.logWriter --condition=None
gcloud projects add-iam-policy-binding ${P} --member=serviceAccount:builder@${P}.iam.gserviceaccount.com --role=roles/artifactregistry.writer --condition=None
gcloud projects add-iam-policy-binding ${P} --member=serviceAccount:builder@${P}.iam.gserviceaccount.com --role=roles/storage.objectAdmin --condition=None
gcloud projects add-iam-policy-binding ${P} --member=serviceAccount:review-web@${P}.iam.gserviceaccount.com --role=roles/aiplatform.user --condition=None
gcloud projects add-iam-policy-binding ${P} --member=serviceAccount:journal-reader@${P}.iam.gserviceaccount.com --role=roles/bigquery.jobUser --condition=None
```

4. **Service-account IAM** Run as a project owner:

```zsh
P=<your-project>
N=<project-number>
gcloud iam service-accounts add-iam-policy-binding upload-api@${P}.iam.gserviceaccount.com --project=${P} --member=serviceAccount:upload-api@${P}.iam.gserviceaccount.com --role=roles/iam.serviceAccountTokenCreator
gcloud iam service-accounts add-iam-policy-binding pipeline-trigger@${P}.iam.gserviceaccount.com --project=${P} --member=serviceAccount:service-${N}@gcp-sa-eventarc.iam.gserviceaccount.com --role=roles/iam.serviceAccountUser
gcloud iam service-accounts add-iam-policy-binding journal-reader@${P}.iam.gserviceaccount.com --project=${P} --member=serviceAccount:review-web@${P}.iam.gserviceaccount.com --role=roles/iam.serviceAccountTokenCreator
```

   The first lets `upload-api` sign upload URLs as itself. The second lets
   Eventarc invoke the pipeline. The third lets the review app mint short-lived
   tokens for journal reports.

5. **Cloud Run invoker** Run as a project owner:

```zsh
P=<your-project>
gcloud run services add-iam-policy-binding pipeline --project=${P} --region=us-east1 --member=serviceAccount:pipeline-trigger@${P}.iam.gserviceaccount.com --role=roles/run.invoker
```

   Then, after the project IAM and SA IAM grants in steps 3 and 4, create the
   Eventarc trigger if it is still missing:

```zsh
P=<your-project>
gcloud eventarc triggers create pipeline-gcs --project=${P} --location=us-east1 --destination-run-service=pipeline --destination-run-region=us-east1 --event-filters=type=google.cloud.storage.object.v1.finalized --event-filters=bucket=${P}-recordings --service-account=pipeline-trigger@${P}.iam.gserviceaccount.com
```

6. **Custom journal role** (optional, tighter than the preview stand-in).
   The default setup uses table-level `roles/bigquery.dataEditor` for `review-web`
   because custom role creation needs `iam.roles.create`. To switch to the append-only
   role later:

```zsh
P=<your-project>
gcloud iam roles create journalAppender --project=${P} --title="Journal appender" --description="Stream rows into the activity journal; nothing else." --permissions=bigquery.tables.updateData,bigquery.tables.get --stage=GA
bq add-iam-policy-binding --member=serviceAccount:review-web@${P}.iam.gserviceaccount.com --role=projects/${P}/roles/journalAppender ${P}:goalvera_journal.activity
bq remove-iam-policy-binding --member=serviceAccount:review-web@${P}.iam.gserviceaccount.com --role=roles/bigquery.dataEditor ${P}:goalvera_journal.activity
```

7. **Project labels** (skipped earlier). Optional:

```zsh
gcloud alpha projects update <your-project> --update-labels=app=goalvera,env=preview
```

8. **Test logins** — invite-only accounts `admin@staff.example` (admin) and
   `teacher@staff.example` (teacher) already exist, with email verified.
   Random passwords are in Secret Manager only. A project owner can read one:

```zsh
gcloud secrets versions access latest --secret=preview-test-login-admin --project=<your-project>
gcloud secrets versions access latest --secret=preview-test-login-teacher --project=<your-project>
```

   The `preview` environment and the WIF provider accept deployments from
   `main` only, so every push to `main` deploys preview.

## Cost

Expected **about $1–3/month** with Cloud Run at min 0, a software CMEK key
(~$0.06), and light preview traffic. Set a small budget alert (for example $5 at 50/90/100%). An HSM recording-wrap key was not created (that would add about
$2.50/month and is unnecessary for unencrypted sample audio).
