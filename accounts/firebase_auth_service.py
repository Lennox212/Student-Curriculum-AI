import firebase_admin
from firebase_admin import auth
import os
import logging

logger = logging.getLogger(__name__)

class FirebaseAuthService:
    _instance = None
    _initialized = False
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def _initialize(self):
        """Initialize Firebase Admin SDK"""
        if self._initialized:
            return
        
        try:
            if not firebase_admin._apps:
                credential_path = os.path.abspath('firebase-credentials.json')
                if os.path.exists(credential_path):
                    os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = credential_path
                    firebase_admin.initialize_app()
            
            self._initialized = True
            logger.info("Firebase Auth initialized successfully")
        except Exception as e:
            logger.error(f"Firebase Auth initialization failed: {e}")
    
    def create_custom_token(self, uid, additional_claims=None):
        """Create a Firebase custom token for a Django user"""
        self._initialize()
        
        try:
            # Create custom token with or without claims
            if additional_claims:
                custom_token = auth.create_custom_token(
                    uid=str(uid),
                    developer_claims=additional_claims  # Changed from additional_claims
                )
            else:
                custom_token = auth.create_custom_token(uid=str(uid))
            
            return custom_token.decode('utf-8') if isinstance(custom_token, bytes) else custom_token
        except Exception as e:
            logger.error(f"Error creating custom token: {e}")
            return None
    
    def create_or_update_firebase_user(self, django_user):
        """Create or update a Firebase user to match Django user"""
        self._initialize()
        
        try:
            uid = str(django_user.id)
            
            try:
                user = auth.get_user(uid)
                auth.update_user(
                    uid,
                    email=django_user.email if django_user.email else None,
                    display_name=django_user.username
                )
                logger.info(f"Updated Firebase user: {uid}")
                return user
            except auth.UserNotFoundError:
                user = auth.create_user(
                    uid=uid,
                    email=django_user.email if django_user.email else None,
                    display_name=django_user.username
                )
                logger.info(f"Created Firebase user: {uid}")
                return user
                
        except Exception as e:
            logger.error(f"Error creating/updating Firebase user: {e}")
            return None

# Singleton instance
firebase_auth_service = FirebaseAuthService()