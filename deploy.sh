#!/bin/bash

# Script de déploiement pour Moulinette d'Inventaire Sage X3
# Usage: ./deploy.sh [environment]
# Environments: dev, staging, prod

set -e  # Arrêter le script en cas d'erreur

# Configuration
ENVIRONMENT=${1:-dev}
PROJECT_NAME="sage-x3-moulinette"
DOCKER_COMPOSE_FILE="docker-compose.yml"

# Couleurs pour les logs
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Fonction de logging
log() {
    echo -e "${BLUE}[$(date +'%Y-%m-%d %H:%M:%S')]${NC} $1"
}

error() {
    echo -e "${RED}[ERROR]${NC} $1" >&2
}

success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

# Vérification des prérequis
check_prerequisites() {
    log "Vérification des prérequis..."
    
    # Vérifier Docker
    if ! command -v docker &> /dev/null; then
        error "Docker n'est pas installé"
        exit 1
    fi
    
    # Vérifier Docker Compose
    if ! command -v docker-compose &> /dev/null; then
        error "Docker Compose n'est pas installé"
        exit 1
    fi
    
    # Vérifier les fichiers nécessaires
    if [[ ! -f "backend/Dockerfile" ]]; then
        error "Dockerfile backend manquant"
        exit 1
    fi
    
    if [[ ! -f "frontend/Dockerfile" ]]; then
        error "Dockerfile frontend manquant"
        exit 1
    fi
    
    success "Prérequis validés"
}

# Configuration de l'environnement
setup_environment() {
    log "Configuration de l'environnement: $ENVIRONMENT"
    
    case $ENVIRONMENT in
        "dev")
            DOCKER_COMPOSE_FILE="docker-compose.yml"
            ;;
        "staging")
            DOCKER_COMPOSE_FILE="docker-compose.staging.yml"
            ;;
        "prod")
            DOCKER_COMPOSE_FILE="docker-compose.prod.yml"
            ;;
        *)
            error "Environnement non supporté: $ENVIRONMENT"
            error "Environnements disponibles: dev, staging, prod"
            exit 1
            ;;
    esac
    
    if [[ ! -f "$DOCKER_COMPOSE_FILE" ]]; then
        error "Fichier Docker Compose manquant: $DOCKER_COMPOSE_FILE"
        exit 1
    fi
    
    success "Environnement configuré: $ENVIRONMENT"
}

# Nettoyage des anciens conteneurs
cleanup() {
    log "Nettoyage des anciens conteneurs..."
    
    # Arrêter et supprimer les conteneurs existants
    docker-compose -f "$DOCKER_COMPOSE_FILE" down --remove-orphans || true
    
    # Supprimer les images non utilisées (optionnel)
    if [[ "$ENVIRONMENT" == "prod" ]]; then
        docker system prune -f || true
    fi
    
    success "Nettoyage terminé"
}

# Construction des images
build_images() {
    log "Construction des images Docker..."
    
    # Construire les images
    docker-compose -f "$DOCKER_COMPOSE_FILE" build --no-cache
    
    success "Images construites avec succès"
}

# Vérification de la configuration
verify_config() {
    log "Vérification de la configuration..."
    
    # Vérifier les fichiers de configuration backend
    if [[ ! -f "backend/.env.example" ]]; then
        warning "Fichier .env.example manquant dans backend/"
    fi
    
    if [[ ! -f "backend/config/sage_mappings.yaml" ]]; then
        error "Fichier de configuration sage_mappings.yaml manquant"
        exit 1
    fi
    
    # Créer les dossiers de données si nécessaire
    mkdir -p data/{uploads,processed,final,archive,logs,database}
    
    success "Configuration vérifiée"
}

# Démarrage des services
start_services() {
    log "Démarrage des services..."
    
    # Démarrer les services en arrière-plan
    docker-compose -f "$DOCKER_COMPOSE_FILE" up -d
    
    # Attendre que les services soient prêts
    log "Attente du démarrage des services..."
    sleep 10
    
    # Vérifier la santé des services
    check_health
    
    success "Services démarrés avec succès"
}

# Vérification de la santé des services
check_health() {
    log "Vérification de la santé des services..."
    
    # Vérifier le backend
    local backend_health=false
    for i in {1..30}; do
        if curl -f -s http://localhost:5000/api/health > /dev/null 2>&1; then
            backend_health=true
            break
        fi
        sleep 2
    done
    
    if [[ "$backend_health" == "true" ]]; then
        success "Backend opérationnel"
    else
        error "Backend non accessible après 60 secondes"
        show_logs
        exit 1
    fi
    
    # Vérifier le frontend (si pas en mode API only)
    if [[ "$ENVIRONMENT" != "api-only" ]]; then
        local frontend_health=false
        for i in {1..15}; do
            if curl -f -s http://localhost:80 > /dev/null 2>&1; then
                frontend_health=true
                break
            fi
            sleep 2
        done
        
        if [[ "$frontend_health" == "true" ]]; then
            success "Frontend opérationnel"
        else
            warning "Frontend non accessible - vérifiez les logs"
        fi
    fi
}

# Affichage des logs
show_logs() {
    log "Affichage des logs récents..."
    docker-compose -f "$DOCKER_COMPOSE_FILE" logs --tail=50
}

# Affichage du statut
show_status() {
    log "Statut des services:"
    docker-compose -f "$DOCKER_COMPOSE_FILE" ps
    
    echo ""
    log "URLs d'accès:"
    echo "  - Backend API: http://localhost:5000"
    echo "  - Frontend: http://localhost:80"
    echo "  - Health Check: http://localhost:5000/api/health"
    
    echo ""
    log "Commandes utiles:"
    echo "  - Voir les logs: docker-compose -f $DOCKER_COMPOSE_FILE logs -f"
    echo "  - Arrêter: docker-compose -f $DOCKER_COMPOSE_FILE down"
    echo "  - Redémarrer: docker-compose -f $DOCKER_COMPOSE_FILE restart"
}

# Fonction principale
main() {
    log "=== Déploiement Moulinette Sage X3 ==="
    log "Environnement: $ENVIRONMENT"
    
    check_prerequisites
    setup_environment
    verify_config
    cleanup
    build_images
    start_services
    show_status
    
    success "=== Déploiement terminé avec succès ==="
}

# Gestion des signaux
trap 'error "Déploiement interrompu"; exit 1' INT TERM

# Aide
if [[ "$1" == "--help" || "$1" == "-h" ]]; then
    echo "Usage: $0 [environment]"
    echo ""
    echo "Environments disponibles:"
    echo "  dev      - Développement (par défaut)"
    echo "  staging  - Pré-production"
    echo "  prod     - Production"
    echo ""
    echo "Options:"
    echo "  --help, -h    Afficher cette aide"
    echo ""
    echo "Exemples:"
    echo "  $0              # Déploiement en dev"
    echo "  $0 prod         # Déploiement en production"
    exit 0
fi

# Exécution
main "$@"