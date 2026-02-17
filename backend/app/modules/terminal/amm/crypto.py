from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.backends import default_backend
import base64
import os


class CryptoService:
    """Handle encryption/decryption of sensitive data"""
    
    def __init__(self, master_key: str = None):
        """Initialize with master key from environment"""
        self.master_key = master_key or os.getenv("SECRET_KEY")
        if not self.master_key:
            raise ValueError("SECRET_KEY not set in environment")
        
        # Simple Fernet key for session encryption
        key = self.master_key.encode()[:32].ljust(32, b'0')
        self.fernet = Fernet(base64.urlsafe_b64encode(key))
    
    def encrypt(self, data: str) -> str:
        """Encrypt string data (for Redis session storage)"""
        return self.fernet.encrypt(data.encode()).decode()
    
    def decrypt(self, encrypted_data: str) -> str:
        """Decrypt string data (for Redis session storage)"""
        return self.fernet.decrypt(encrypted_data.encode()).decode()
    
    def _derive_key(self, salt: bytes = None) -> tuple[bytes, bytes]:
        """Derive encryption key from master key"""
        if salt is None:
            salt = os.urandom(16)
        
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
            backend=default_backend()
        )
        key = base64.urlsafe_b64encode(kdf.derive(self.master_key.encode()))
        return key, salt
    
    def encrypt_private_key(self, private_key_content: str) -> str:
        """Encrypt private key content (legacy - for database storage)"""
        key, salt = self._derive_key()
        fernet = Fernet(key)
        
        encrypted = fernet.encrypt(private_key_content.encode())
        # Store salt + encrypted data
        combined = base64.b64encode(salt + encrypted).decode()
        return combined
    
    def decrypt_private_key(self, encrypted_data: str) -> str:
        """Decrypt private key content (legacy - for database storage)"""
        combined = base64.b64decode(encrypted_data.encode())
        salt = combined[:16]
        encrypted = combined[16:]
        
        key, _ = self._derive_key(salt)
        fernet = Fernet(key)
        
        decrypted = fernet.decrypt(encrypted)
        return decrypted.decode()