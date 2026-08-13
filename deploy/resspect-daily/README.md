# RESSPECT daily CronJob

## Local setup

This CronJob can be run on a local kind cluster (for testing purposes)

From the repository root, build and load the image:
```bash
docker build -f deploy/resspect-daily/Dockerfile -t resspect-daily:dev .
kind load docker-image resspect-daily:dev --name fastdb-local
```
The FASTDB client reads credentials from `~/.fastdb.ini`. The profile selected
by `fastdb.profile` in `helm/resspect-daily/values-kind.yaml` must contain
`url`, `username`, and `password`, and its URL must be reachable from inside the
Kubernetes cluster.
For example, a local Kind deployment in the same namespace as FASTDB can use:
```ini
[local-kind-cronjob]
url = http://webap:8080
username = YOUR_FASTDB_USERNAME
password = YOUR_FASTDB_PASSWORD
```

where to profile name should match that in the values file `helm/resspect-daily/values-kind.yaml`. A production profile may instead use the externally reachable HTTPS address of its FASTDB web app.

Create the Secret from the configured INI file:

```bash
kubectl create secret generic resspect-fastdb \
  --namespace fastdb-local \
  --from-file=fastdb.ini="$HOME/.fastdb.ini"
```

Install the CronJob (this job is suspended and a `--dry-run` by default) with Helm:

```bash
helm upgrade --install resspect-daily \
  deploy/resspect-daily/helm/resspect-daily \
  --namespace fastdb-local \
  --values deploy/resspect-daily/helm/resspect-daily/values-kind.yaml
```

Run it once and view the logs:

```bash
kubectl create job --from=cronjob/resspect-daily resspect-daily-manual \
  --namespace fastdb-local
kubectl logs --namespace fastdb-local job/resspect-daily-manual --all-containers
```

To unsuspend the job, remove the dry-run flag, or change the schedule edit the following lines in the values file (e.g. `helm/resspect-daily/values-kind.yaml`)
```bash
schedule: "0 12 * * *"
timeZone: America/Los_Angeles
suspend: true
dryRun: true
```

## Production runs

For a production cluster:

1. Build the image for the cluster's CPU architecture and push it to a registry
   accessible by the cluster.
2. Create a production values file containing the published image, pull policy,
   FASTDB profile, and scientific configuration.
3. Provide the FASTDB INI file as a Secret using the method required by the
   cluster.
4. Install the chart with that values file using the cluster's normal Helm
   deployment process.

```bash
helm upgrade --install resspect-daily \
  deploy/resspect-daily/helm/resspect-daily \
  --namespace YOUR_NAMESPACE \
  --values YOUR_VALUES.yaml
```
