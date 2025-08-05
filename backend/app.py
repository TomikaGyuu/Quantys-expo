import os
import json
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import pandas as pd
from datetime import datetime, date
import uuid
from werkzeug.utils import secure_filename
import logging
from dotenv import load_dotenv

# Charger les variables d'environnement
load_dotenv()

# Imports des services
from services.session_service import SessionService
from services.file_processor import FileProcessorService
from utils.validators import FileValidator
from database import db_manager

app = Flask(__name__)
CORS(app, expose_headers=['Content-Disposition'])

# Configuration améliorée
class Config:
    def __init__(self):
        self.UPLOAD_FOLDER = os.getenv('UPLOAD_FOLDER', 'uploads')
        self.PROCESSED_FOLDER = os.getenv('PROCESSED_FOLDER', 'processed')
        self.FINAL_FOLDER = os.getenv('FINAL_FOLDER', 'final')
        self.ARCHIVE_FOLDER = os.getenv('ARCHIVE_FOLDER', 'archive')
        self.LOG_FOLDER = os.getenv('LOG_FOLDER', 'logs')
        self.MAX_FILE_SIZE = int(os.getenv('MAX_FILE_SIZE', 16 * 1024 * 1024))
        self.SECRET_KEY = os.getenv('SECRET_KEY', 'dev-key-change-in-production')
        
        # Créer les répertoires
        for folder in [self.UPLOAD_FOLDER, self.PROCESSED_FOLDER,
                      self.FINAL_FOLDER, self.ARCHIVE_FOLDER, self.LOG_FOLDER]:
            os.makedirs(folder, exist_ok=True)

config = Config()
app.config.from_object(config)
app.secret_key = config.SECRET_KEY

# Configuration du logging améliorée
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(config.LOG_FOLDER, 'inventory_processor.log')),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Initialisation des services
session_service = SessionService()
file_processor = FileProcessorService()

# Classe de compatibilité (pour migration progressive)
class SageX3Processor:
    """
    Classe de compatibilité - utilise maintenant les services
    """
    def __init__(self):
        self.session_service = session_service
        self.file_processor = file_processor
        # Dictionnaire temporaire pour compatibilité
        self.sessions = {}

# Initialisation du processeur
processor = SageX3Processor()

# Endpoints API
@app.route('/api/upload', methods=['POST'])
def upload_file():
    """Endpoint amélioré pour l'upload initial d'un fichier Sage X3"""
    if 'file' not in request.files:
        return jsonify({'error': 'Aucun fichier fourni'}), 400
    
    file = request.files['file']
    if not file.filename:
        return jsonify({'error': 'Nom de fichier vide'}), 400
    
    file_extension = os.path.splitext(file.filename)[1].lower()
    if file_extension not in ['.csv', '.xlsx', '.xls']:
        return jsonify({'error': 'Format non supporté. Seuls CSV et XLSX sont acceptés'}), 400
    
    # Validation sécurisée du fichier
    is_valid, error_msg = FileValidator.validate_file_security(file, config.MAX_FILE_SIZE)
    if not is_valid:
        return jsonify({'error': error_msg}), 400
    
    session_creation_timestamp = datetime.now()
    filepath = None
    
    try:
        # Créer la session en base de données
        session_id = session_service.create_session(
            original_filename=file.filename,
            original_file_path='',  # Sera mis à jour après sauvegarde
            status='uploading'
        )
        
        filename_on_disk = secure_filename(f"{session_id}_{file.filename}")
        filepath = os.path.join(config.UPLOAD_FOLDER, filename_on_disk)
        file.save(filepath)
        
        # Mettre à jour le chemin du fichier
        session_service.update_session(session_id, original_file_path=filepath)
        
        # Traitement du fichier
        is_valid, result_data, headers, inventory_date = file_processor.validate_and_process_sage_file(
            filepath, file_extension, session_creation_timestamp
        )
        
        if not is_valid:
            if os.path.exists(filepath):
                os.remove(filepath)
            session_service.delete_session(session_id)
            return jsonify({'error': str(result_data)}), 400
        
        original_df = result_data
        
        # Agrégation
        aggregated_df = file_processor.aggregate_data(original_df)
        
        # Génération du template
        template_file_path = file_processor.generate_template(
            aggregated_df, session_id, config.PROCESSED_FOLDER
        )
        
        # Mise à jour de la session
        session_service.update_session(
            session_id,
            template_file_path=template_file_path,
            status='template_generated',
            inventory_date=inventory_date,
            nb_articles=len(aggregated_df),
            nb_lots=len(original_df),
            total_quantity=float(aggregated_df['Quantite_Theorique_Totale'].sum()),
            header_lines=json.dumps(headers)
        )
        
        # Compatibilité temporaire
        processor.sessions[session_id] = {
            'original_df': original_df,
            'aggregated_df': aggregated_df,
            'header_lines': headers
        }

        return jsonify({
            'success': True,
            'session_id': session_id,
            'template_url': f"/api/download/template/{session_id}",
            'stats': {
                'nb_articles': len(aggregated_df),
                'total_quantity': float(aggregated_df['Quantite_Theorique_Totale'].sum()),
                'nb_lots': len(original_df),
                'inventory_date': inventory_date.isoformat() if inventory_date else None
            }
        })
    
    except Exception as e:
        logger.error(f"Erreur upload: {e}", exc_info=True)
        if filepath and os.path.exists(filepath):
            os.remove(filepath)
        return jsonify({'error': 'Erreur interne du serveur'}), 500

@app.route('/api/process', methods=['POST'])
def process_completed_file_route():
    """Endpoint amélioré pour traiter le fichier complété"""
    if 'file' not in request.files or 'session_id' not in request.form:
        return jsonify({'error': 'Paramètres manquants'}), 400
    
    try:
        session_id = request.form['session_id']
        file = request.files['file']
        strategy = request.form.get('strategy', 'FIFO')
        
        # Vérifier que la session existe
        session = session_service.get_session(session_id)
        if not session:
            return jsonify({'error': 'Session non trouvée'}), 404
        
        if not file.filename.lower().endswith(('.xlsx', '.xls')):
            return jsonify({'error': 'Seuls les fichiers Excel sont acceptés'}), 400
        
        # Validation du fichier complété
        temp_filepath = os.path.join(config.PROCESSED_FOLDER, f"temp_{session_id}_{file.filename}")
        file.save(temp_filepath)
        
        is_valid, validation_msg, errors = file_processor.validate_completed_template(temp_filepath)
        if not is_valid:
            os.remove(temp_filepath)
            return jsonify({'error': validation_msg, 'details': errors}), 400
        
        filename_on_disk = secure_filename(f"completed_{session_id}_{file.filename}")
        filepath = os.path.join(config.PROCESSED_FOLDER, filename_on_disk)
        os.rename(temp_filepath, filepath)
        
        # Traitement (utilise encore l'ancienne méthode pour compatibilité)
        processed_summary_df = processor.process_completed_file(session_id, filepath)
        distributed_summary_df = processor.distribute_discrepancies(session_id, strategy)
        final_file_path = processor.generate_final_file(session_id)
        
        # Mise à jour de la session en base
        session_service.update_session(
            session_id,
            completed_file_path=filepath,
            final_file_path=final_file_path,
            status='completed',
            strategy_used=strategy
        )
        
        session_data = processor.sessions.get(session_id, {})

        return jsonify({
            'success': True,
            'final_url': f"/api/download/final/{session_id}",
            'stats': {
                'total_discrepancy': session_data.get('total_discrepancy', 0),
                'adjusted_items': session_data.get('adjusted_items_count', 0),
                'strategy_used': session_data.get('strategy_used', 'N/A')
            }
        })
    
    except ValueError as e:
        logger.error(f"Erreur validation: {e}")
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Erreur traitement: {e}", exc_info=True)
        return jsonify({'error': 'Erreur interne du serveur'}), 500

@app.route('/api/download/<file_type>/<session_id>', methods=['GET'])
def download_file(file_type: str, session_id: str):
    """Endpoint de téléchargement amélioré"""
    try:
        session = session_service.get_session(session_id)
        if not session:
            return jsonify({'error': 'Session non trouvée'}), 404
        
        filepath = None
        download_name = None
        mimetype = None

        if file_type == 'template':
            filepath = session.template_file_path
            if not filepath:
                return jsonify({'error': 'Template non généré'}), 404
            download_name = os.path.basename(filepath)
            mimetype = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        elif file_type == 'final':
            filepath = session.final_file_path
            if not filepath:
                return jsonify({'error': 'Fichier final non généré'}), 404
            download_name = os.path.basename(filepath)
            mimetype = 'text/csv'
        else:
            return jsonify({'error': 'Type de fichier invalide'}), 400
        
        if not os.path.exists(filepath):
            return jsonify({'error': 'Fichier non trouvé sur le serveur'}), 404
        
        return send_file(
            filepath,
            as_attachment=True,
            download_name=download_name,
            mimetype=mimetype
        )
    
    except Exception as e:
        logger.error(f"Erreur téléchargement: {e}", exc_info=True)
        return jsonify({'error': 'Erreur interne du serveur'}), 500

@app.route('/api/sessions', methods=['GET'])
def list_sessions():
    """Liste les sessions avec pagination"""
    try:
        limit = int(request.args.get('limit', 50))
        include_expired = request.args.get('include_expired', 'false').lower() == 'true'
        
        sessions_list = session_service.list_sessions(limit=limit, include_expired=include_expired)
        
        return jsonify({'sessions': sessions_list})
    
    except Exception as e:
        logger.error(f"Erreur listage sessions: {e}", exc_info=True)
        return jsonify({'error': 'Erreur interne du serveur'}), 500

@app.route('/api/sessions/<session_id>', methods=['DELETE'])
def delete_session(session_id: str):
    """Supprime une session"""
    try:
        success = session_service.delete_session(session_id)
        if success:
            # Nettoyer aussi la compatibilité temporaire
            if session_id in processor.sessions:
                del processor.sessions[session_id]
            return jsonify({'success': True})
        else:
            return jsonify({'error': 'Session non trouvée'}), 404
    except Exception as e:
        logger.error(f"Erreur suppression session: {e}")
        return jsonify({'error': 'Erreur interne du serveur'}), 500

@app.route('/api/analyze/<session_id>', methods=['GET'])
def analyze_file_format(session_id: str):
    """Endpoint pour analyser le format d'un fichier uploadé"""
    try:
        session = session_service.get_session(session_id)
        if not session:
            return jsonify({'error': 'Session non trouvée'}), 404
        
        filepath = session.original_file_path
        if not os.path.exists(filepath):
            return jsonify({'error': 'Fichier non trouvé'}), 404
        
        format_detected, format_msg, format_info = file_processor.detect_file_format(filepath)
        
        return jsonify({
            'success': format_detected,
            'message': format_msg,
            'format_info': format_info,
            'expected_format': {
                'columns_required': len(file_processor.SAGE_COLUMN_NAMES_ORDERED),
                'column_names': file_processor.SAGE_COLUMN_NAMES_ORDERED,
                'expected_line_types': ['E', 'L', 'S']
            }
        })
        
    except Exception as e:
        logger.error(f"Erreur analyse format: {e}")
        return jsonify({'error': 'Erreur interne du serveur'}), 500

@app.route('/api/health', methods=['GET'])
def health_check():
    """Endpoint de santé amélioré"""
    try:
        db_healthy = db_manager.health_check()
        sessions_count = len(session_service.list_sessions(limit=1000))
        
        status = 'healthy' if db_healthy else 'degraded'
        
        return jsonify({
            'status': status,
            'timestamp': datetime.now().isoformat(),
            'database': 'healthy' if db_healthy else 'error',
            'active_sessions_count': sessions_count
        })
    except Exception as e:
        logger.error(f"Health check error: {e}")
        return jsonify({
            'status': 'error',
            'timestamp': datetime.now().isoformat(),
            'error': str(e)
        }), 500

# Tâche de nettoyage (à exécuter périodiquement)
@app.route('/api/cleanup', methods=['POST'])
def cleanup_sessions():
    """Nettoie les sessions expirées"""
    try:
        hours = int(request.json.get('hours', 24))
        count = session_service.cleanup_expired_sessions(hours)
        return jsonify({'cleaned_sessions': count})
    except Exception as e:
        logger.error(f"Erreur nettoyage: {e}")
        return jsonify({'error': 'Erreur nettoyage'}), 500

if __name__ == '__main__':
    logger.info("Démarrage de l'application Moulinette Sage X3")
    app.run(host='0.0.0.0', port=5000, debug=True)
