# Vault policy "events-read" — read-only access to every service's KV-v2 secret.
#
# KV-v2 stores data under <mount>/data/<path>, so the path here is
# secret/data/events/* (NOT secret/events/*). The metadata path is granted read
# so ESO can list versions. Applied in vault-bootstrap.md and bound to the
# Kubernetes auth role "events".

path "secret/data/events/*" {
  capabilities = ["read"]
}

path "secret/metadata/events/*" {
  capabilities = ["read", "list"]
}
