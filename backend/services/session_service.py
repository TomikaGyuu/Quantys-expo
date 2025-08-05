import json
from datetime import datetime, timedelta
from sqlalchemy.orm import Session as DBSession
from models.session import Session
from models.inventory_item import InventoryItem
from database import db_manager
import logging

logger = logging.getLogger(__name__)

class SessionService:
    def __init__(self):
        self.db = db_manager
    
    def create_session(self, original_filename: str, original_file_path: str, **kwargs) -> str:
        """Crée une nouvelle session en base de données"""
        db_session = self.db.get_session()
        try:
            session = Session(
                original_filename=original_filename,
                original_file_path=original_file_path,
                **kwargs
            )
            db_session.add(session)
            db_session.commit()
            
            logger.info(f"Session créée: {session.id}")
            return session.id
        except Exception as e:
            db_session.rollback()
            logger.error(f"Erreur création session: {e}")
            raise
        finally:
            db_session.close()
    
    def get_session(self, session_id: str) -> Session:
        """Récupère une session par ID"""
        db_session = self.db.get_session()
        try:
            session = db_session.query(Session).filter(Session.id == session_id).first()
            if session:
                # Mettre à jour last_accessed
                session.last_accessed = datetime.utcnow()
                db_session.commit()
            return session
        except Exception as e:
            logger.error(f"Erreur récupération session {session_id}: {e}")
            return None
        finally:
            db_session.close()
    
    def update_session(self, session_id: str, **updates) -> bool:
        """Met à jour une session"""
        db_session = self.db.get_session()
        try:
            session = db_session.query(Session).filter(Session.id == session_id).first()
            if not session:
                return False
            
            for key, value in updates.items():
                if hasattr(session, key):
                    setattr(session, key, value)
            
            session.updated_at = datetime.utcnow()
            session.last_accessed = datetime.utcnow()
            db_session.commit()
            
            logger.info(f"Session {session_id} mise à jour")
            return True
        except Exception as e:
            db_session.rollback()
            logger.error(f"Erreur mise à jour session {session_id}: {e}")
            return False
        finally:
            db_session.close()
    
    def list_sessions(self, limit: int = 50, include_expired: bool = False) -> list:
        """Liste les sessions"""
        db_session = self.db.get_session()
        try:
            query = db_session.query(Session)
            
            if not include_expired:
                cutoff_time = datetime.utcnow() - timedelta(hours=24)
                query = query.filter(Session.last_accessed > cutoff_time)
            
            sessions = query.order_by(Session.created_at.desc()).limit(limit).all()
            return [session.to_dict() for session in sessions]
        except Exception as e:
            logger.error(f"Erreur listage sessions: {e}")
            return []
        finally:
            db_session.close()
    
    def delete_session(self, session_id: str) -> bool:
        """Supprime une session et ses données associées"""
        db_session = self.db.get_session()
        try:
            # Supprimer les items d'inventaire
            db_session.query(InventoryItem).filter(InventoryItem.session_id == session_id).delete()
            
            # Supprimer la session
            session = db_session.query(Session).filter(Session.id == session_id).first()
            if session:
                db_session.delete(session)
                db_session.commit()
                logger.info(f"Session {session_id} supprimée")
                return True
            return False
        except Exception as e:
            db_session.rollback()
            logger.error(f"Erreur suppression session {session_id}: {e}")
            return False
        finally:
            db_session.close()
    
    def cleanup_expired_sessions(self, hours: int = 24):
        """Nettoie les sessions expirées"""
        db_session = self.db.get_session()
        try:
            cutoff_time = datetime.utcnow() - timedelta(hours=hours)
            
            # Récupérer les sessions expirées
            expired_sessions = db_session.query(Session).filter(
                Session.last_accessed < cutoff_time
            ).all()
            
            count = 0
            for session in expired_sessions:
                # Supprimer les items associés
                db_session.query(InventoryItem).filter(
                    InventoryItem.session_id == session.id
                ).delete()
                
                # Supprimer la session
                db_session.delete(session)
                count += 1
            
            db_session.commit()
            logger.info(f"{count} sessions expirées supprimées")
            return count
        except Exception as e:
            db_session.rollback()
            logger.error(f"Erreur nettoyage sessions: {e}")
            return 0
        finally:
            db_session.close()
    
    def save_inventory_items(self, session_id: str, items_data: list):
        """Sauvegarde les items d'inventaire en base"""
        db_session = self.db.get_session()
        try:
            # Supprimer les anciens items de cette session
            db_session.query(InventoryItem).filter(InventoryItem.session_id == session_id).delete()
            
            # Ajouter les nouveaux items
            for item_data in items_data:
                item = InventoryItem(session_id=session_id, **item_data)
                db_session.add(item)
            
            db_session.commit()
            logger.info(f"{len(items_data)} items sauvegardés pour session {session_id}")
        except Exception as e:
            db_session.rollback()
            logger.error(f"Erreur sauvegarde items session {session_id}: {e}")
            raise
        finally:
            db_session.close()
    
    def get_inventory_items(self, session_id: str) -> list:
        """Récupère les items d'inventaire d'une session"""
        db_session = self.db.get_session()
        try:
            items = db_session.query(InventoryItem).filter(
                InventoryItem.session_id == session_id
            ).all()
            return [item.to_dict() for item in items]
        except Exception as e:
            logger.error(f"Erreur récupération items session {session_id}: {e}")
            return []
        finally:
            db_session.close()