# deploy/scripts/Makefile — helm/k8s workflow for the events platform.
#
#   make lint       helm lint every chart + kubeconform the rendered output
#   make template   render both umbrellas (prod overlay) to stdout
#   make bootstrap  helm upgrade --install the cluster prereqs, in order
#   make seed       seed Vault from .env.example (deploy/scripts/seed-vault.sh)
#   make smoke      spin up a kind cluster, deploy, assert receiver 202, tear down
#   make clean      delete the kind smoke cluster
#
# Override KUBECONFORM_FLAGS / VALUES / etc. on the command line as needed.

SHELL := /usr/bin/env bash
# Repo layout: this Makefile lives in deploy/scripts/.
SCRIPTS_DIR := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
DEPLOY_DIR  := $(abspath $(SCRIPTS_DIR)/..)
REPO_ROOT   := $(abspath $(DEPLOY_DIR)/..)
HELM_DIR    := $(DEPLOY_DIR)/helm

LIBRARY        := $(HELM_DIR)/library/events-common
SERVICE_CHARTS := $(wildcard $(HELM_DIR)/charts/*)
PLATFORM       := $(HELM_DIR)/umbrella/events-platform
OBSERVABILITY  := $(HELM_DIR)/umbrella/events-observability

KUBECONFORM_FLAGS ?= -ignore-missing-schemas -summary
KIND_CLUSTER      ?= events-smoke

.PHONY: lint template bootstrap seed smoke clean deps help

help:
	@grep -E '^#   make ' $(lastword $(MAKEFILE_LIST)) | sed 's/^#   /  /'

## deps — build umbrella subchart dependencies (needed before template/lint).
deps:
	helm dependency build $(PLATFORM)
	helm dependency build $(OBSERVABILITY)

## lint — helm lint all charts + kubeconform the rendered umbrellas.
lint:
	helm lint $(LIBRARY)
	@for c in $(SERVICE_CHARTS); do helm lint $$c || exit 1; done
	helm lint $(PLATFORM) -f $(PLATFORM)/values-prod.yaml
	helm lint $(PLATFORM) -f $(PLATFORM)/values-kind.yaml
	helm lint $(OBSERVABILITY) -f $(OBSERVABILITY)/values-prod.yaml
	@echo "--- kubeconform: events-platform (prod) ---"
	helm template events-platform $(PLATFORM) -f $(PLATFORM)/values-prod.yaml | kubeconform $(KUBECONFORM_FLAGS)
	@echo "--- kubeconform: events-platform (kind) ---"
	helm template events-platform $(PLATFORM) -f $(PLATFORM)/values-kind.yaml | kubeconform $(KUBECONFORM_FLAGS)
	@echo "--- kubeconform: events-observability (prod) ---"
	helm template events-observability $(OBSERVABILITY) -f $(OBSERVABILITY)/values-prod.yaml | kubeconform $(KUBECONFORM_FLAGS)

## template — render both umbrellas with the prod overlay to stdout.
template:
	helm template events-platform $(PLATFORM) -f $(PLATFORM)/values-prod.yaml
	helm template events-observability $(OBSERVABILITY) -f $(OBSERVABILITY)/values-prod.yaml

## bootstrap — install cluster prereqs in order (cert-manager -> ingress -> vault -> ESO).
bootstrap:
	helm repo add jetstack https://charts.jetstack.io
	helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx
	helm repo add hashicorp https://helm.releases.hashicorp.com
	helm repo add external-secrets https://charts.external-secrets.io
	helm repo update
	helm upgrade --install cert-manager jetstack/cert-manager \
	  --version v1.19.5 --namespace cert-manager --create-namespace \
	  -f $(HELM_DIR)/prereqs/cert-manager-values.yaml
	helm upgrade --install ingress-nginx ingress-nginx/ingress-nginx \
	  --version 4.15.1 --namespace ingress-nginx --create-namespace \
	  -f $(HELM_DIR)/prereqs/ingress-nginx-values.yaml
	helm upgrade --install vault hashicorp/vault \
	  --version 0.30.1 --namespace vault --create-namespace \
	  -f $(HELM_DIR)/prereqs/vault-values.yaml
	helm upgrade --install external-secrets external-secrets/external-secrets \
	  --version 0.10.7 --namespace external-secrets --create-namespace \
	  -f $(HELM_DIR)/prereqs/eso-values.yaml

## seed — populate Vault from .env.example dev defaults.
seed:
	$(SCRIPTS_DIR)/seed-vault.sh

## smoke — full kind end-to-end smoke (creates + tears down a kind cluster).
smoke:
	$(SCRIPTS_DIR)/smoke.sh

## clean — delete the kind smoke cluster if it exists.
clean:
	-kind delete cluster --name $(KIND_CLUSTER)
