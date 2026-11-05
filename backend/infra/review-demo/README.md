# Review demo deployment handoff

The shared Review UI is hosted at https://35-95-62-9.sslip.io/ui/.
Deployment settings below identify the workshop environment.

## Current deployment

- AWS region is us-west-2.
- Account is 232549714284.
- EC2 instance is i-07d5f8791f4e1b1d3.
- Security group is sg-0b00e26cb01d373a1.
- The current public IP is 35.95.62.9. An instance stop/start can change this IP.
- Backend image is 232549714284.dkr.ecr.us-west-2.amazonaws.com/re-server@sha256:3c2ceb69f6a72d7b0bb6c170a76bdb810445aaaae10edca1ec17235511a15faf.
- Source commit deployed is 38ea611.
- Backend container is bevijen-backend.
- HTTPS proxy container is bevijen-https and uses the official caddy:2 image.
- Caddy data and configuration persist in Docker volumes bevijen-caddy-data and bevijen-caddy-config.
- Backend listens on 127.0.0.1:8000 with Uvicorn proxy headers trusted only from 127.0.0.1.
- TCP 80 and 443 are open publicly. Caddy forwards HTTPS to the loopback backend and blocks /tools and /tools/*.
- Cognito pool is us-west-2_wY6YuC6fi.
- Cognito client ID is 4llbgsbhq1nk8p6n0js0c468a8.
- Cognito domain is bevijen-review-232549714284.auth.us-west-2.amazoncognito.com.
- Callback URL is https://35-95-62-9.sslip.io/auth/callback.
- UI_URL is /ui/ and AUTH_MODE is cognito.
- Cognito group specialist grants the review role. Batch recheck requires admin in the current backend.
- RE_SQS_VERIFY_URL points to https://sqs.us-west-2.amazonaws.com/232549714284/bevijen-pacifica-demo-verify.
- Preserve the existing AWS storage and KMS environment settings when replacing the backend container.

## Demonstration scope and verification

Sample 1 and Sample 2 were uploaded through POST /ingest/ndp/upload in AWS.
The runs inserted 20 and 16 services respectively.
The original NDP payloads are archived through the ingestion pipeline.
The public HTTPS page, unauthenticated HTTP 401 response for /records, and successful Cognito sign-in returning to /ui/ were verified.
Per-specialist resolved_by attribution must still be verified after a signed-in user submits a review decision.

Pacifica Public School Education uses synthetic language evidence.
Its service ID is 1402495e-ea43-5af8-8d6a-c34d6dadb5a7.
Its pending finding ID at the handoff was 256bb1f9-0f4f-4605-b252-40c35a23dbaf.
The synthetic evidence page is /ui/demo-evidence.html.
The dedicated worker bevijen-pacifica-demo-worker supports this service only.
Use its single-row recheck for the demo. A batch containing other services makes this worker exit and leaves the unexpected message unacknowledged.
The verified flow is UI recheck, SQS, AgentCore/Nova comparison, preserved open finding, and SQS acknowledgement.

## Supporting files

Caddyfile records the HTTPS configuration.
add-specialists.sh configures the two requested accounts and emails invitations for newly created accounts. Existing accounts retain their credentials.
start-pacifica-demo-worker.sh is the historical workshop launcher. It requires /tmp/bevijen-test-queued-finding.json from the original CloudShell session. It is not yet a standalone reproducible deployment script.
These resources were provisioned through CLI and SSM. They have not been imported into Terraform state.

## Repository contents

Commit the deployment notes, Caddyfile, launcher, and reviewed account setup script.
The account script contains the two requested university email addresses; omit that script from a public repository if those personal addresses should remain private.
The UI files backend/app/ui/index.html and backend/app/ui/demo-evidence.html and login callback implementation already exist in the shared repository.
Do not commit passwords, AWS credentials, JWTs, exported container environment files, Terraform state, or .terraform/ provider files.
The Terraform provider lock file can be committed after reviewing the team's current Terraform configuration.
