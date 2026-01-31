import io
import threading
from collections import OrderedDict
import boto3
from botocore.exceptions import ClientError
from flask import current_app
from PIL import Image
from app.config import Config

# Image compression settings
MAX_IMAGE_HEIGHT = 1080
JPEG_QUALITY = 85

# Cache settings (adjustable for VPS with limited resources)
CACHE_MAX_SIZE_BYTES = 50 * 1024 * 1024  # 50MB max total cache size
CACHE_MAX_ITEMS = 100  # Max 100 images in cache


class ImageCache:
    """
    Thread-safe LRU cache for images with size limits.
    Evicts least recently used items when limits are exceeded.
    """
    
    def __init__(self, max_size_bytes=CACHE_MAX_SIZE_BYTES, max_items=CACHE_MAX_ITEMS):
        self._cache = OrderedDict()  # key -> (data, content_type, size)
        self._lock = threading.Lock()
        self._max_size_bytes = max_size_bytes
        self._max_items = max_items
        self._current_size = 0
    
    def get(self, key):
        """Get item from cache, returns (data, content_type) or None"""
        with self._lock:
            if key in self._cache:
                # Move to end (most recently used)
                self._cache.move_to_end(key)
                data, content_type, _ = self._cache[key]
                return data, content_type
            return None
    
    def set(self, key, data, content_type):
        """Add item to cache with LRU eviction"""
        size = len(data)
        
        # Don't cache items larger than 25% of max cache size
        if size > self._max_size_bytes * 0.25:
            return
        
        with self._lock:
            # Remove existing entry if present
            if key in self._cache:
                _, _, old_size = self._cache.pop(key)
                self._current_size -= old_size
            
            # Evict LRU items until we have space
            while (self._current_size + size > self._max_size_bytes or 
                   len(self._cache) >= self._max_items):
                if not self._cache:
                    break
                oldest_key, (_, _, oldest_size) = self._cache.popitem(last=False)
                self._current_size -= oldest_size
            
            # Add new item
            self._cache[key] = (data, content_type, size)
            self._current_size += size
    
    def delete(self, key):
        """Remove item from cache"""
        with self._lock:
            if key in self._cache:
                _, _, size = self._cache.pop(key)
                self._current_size -= size
    
    def stats(self):
        """Return cache statistics"""
        with self._lock:
            return {
                'items': len(self._cache),
                'size_bytes': self._current_size,
                'size_mb': round(self._current_size / (1024 * 1024), 2),
                'max_size_mb': self._max_size_bytes / (1024 * 1024)
            }


# Global cache instance
_image_cache = ImageCache()

class S3Service:
    def __init__(self):
        self.s3_client = boto3.client(
            's3',
            endpoint_url=Config.B2_ENDPOINT_URL,
            aws_access_key_id=Config.B2_KEY_ID,
            aws_secret_access_key=Config.B2_APP_KEY
        )
        self.bucket_name = Config.B2_BUCKET_NAME

    def upload_file(self, file_obj, filename, folder="", content_type=None):
        """
        Uploads a file-like object to B2.
        :param file_obj: File-like object (bytes)
        :param filename: Desired filename in bucket
        :param folder: Optional folder prefix (e.g. 'party_logos')
        :param content_type: MIME type of the file
        :return: The full key (path) of the uploaded file or None if failed
        """
        try:
            full_key = f"{folder}/{filename}" if folder else filename
            extra_args = {}
            if content_type:
                extra_args['ContentType'] = content_type

            self.s3_client.upload_fileobj(
                file_obj,
                self.bucket_name,
                full_key,
                ExtraArgs=extra_args
            )
            current_app.logger.info(f"S3 Upload Success: {full_key}")
            return full_key
        except ClientError as e:
            current_app.logger.error(f"S3 Upload Error: {e}")
            return None

    def delete_file(self, filename):
        """
        Deletes a file from B2 and removes from cache.
        :param filename: Full key of the file to delete
        """
        try:
            current_app.logger.info(f"S3 Delete: Attempting to delete {filename}")
            self.s3_client.delete_object(Bucket=self.bucket_name, Key=filename)
            # Also remove from cache
            _image_cache.delete(filename)
            current_app.logger.info(f"S3 Delete Success: {filename}")
            return True
        except ClientError as e:
            current_app.logger.error(f"S3 Delete Error for {filename}: {e}")
            return False
    
    def get_file_cached(self, filename):
        """
        Get file with caching. Returns (data, content_type) or (None, None).
        Checks cache first, fetches from S3 if not cached.
        """
        import mimetypes
        
        # Check cache first
        cached = _image_cache.get(filename)
        if cached:
            current_app.logger.debug(f"Cache HIT: {filename}")
            return cached
        
        current_app.logger.debug(f"Cache MISS: {filename}")
        
        # Fetch from S3
        try:
            response = self.s3_client.get_object(
                Bucket=self.bucket_name,
                Key=filename
            )
            
            data = response['Body'].read()
            content_type = response.get('ContentType', 'application/octet-stream')
            
            if not content_type or content_type == 'application/octet-stream':
                guessed_type, _ = mimetypes.guess_type(filename)
                if guessed_type:
                    content_type = guessed_type
            
            # Store in cache
            _image_cache.set(filename, data, content_type)
            
            return data, content_type
            
        except ClientError as e:
            current_app.logger.error(f"S3 Get Error for {filename}: {e}")
            return None, None

    def get_presigned_url(self, filename, expiration=3600):
        """
        Generates a presigned URL for downloading a file.
        :param filename: Full key of the file
        :param expiration: Time in seconds for the URL to remain valid
        :return: Presigned URL string
        """
        try:
            response = self.s3_client.generate_presigned_url(
                'get_object',
                Params={'Bucket': self.bucket_name, 'Key': filename},
                ExpiresIn=expiration
            )
            return response
        except ClientError as e:
            current_app.logger.error(f"S3 Presigned URL Error: {e}")
            return None


def compress_image(file_stream, original_filename):
    """
    Compresses and optimizes an image before upload.
    - Resizes to max 1080px height (maintains aspect ratio)
    - Converts to JPEG if no transparency, keeps PNG otherwise
    
    :param file_stream: File stream from request.files
    :param original_filename: Original filename for extension detection
    :return: Tuple of (BytesIO, new_filename, content_type)
    """
    try:
        img = Image.open(file_stream)
        original_format = img.format or 'PNG'
        current_app.logger.info(f"Processing image: {original_filename}, mode: {img.mode}, size: {img.size}")
        
        # Check for ACTUAL transparency (not just mode)
        has_transparency = False
        if img.mode == 'RGBA':
            # Check if any pixel has alpha < 255
            alpha = img.getchannel('A')
            if alpha.getextrema()[0] < 255:  # Min alpha value < 255 means transparency exists
                has_transparency = True
                current_app.logger.info("Image has actual transparent pixels")
        elif img.mode == 'LA':
            alpha = img.getchannel('A')
            if alpha.getextrema()[0] < 255:
                has_transparency = True
        elif img.mode == 'P' and 'transparency' in img.info:
            has_transparency = True
        
        # Resize if height exceeds max
        if img.height > MAX_IMAGE_HEIGHT:
            ratio = MAX_IMAGE_HEIGHT / img.height
            new_width = int(img.width * ratio)
            img = img.resize((new_width, MAX_IMAGE_HEIGHT), Image.Resampling.LANCZOS)
            current_app.logger.info(f"Image resized to {new_width}x{MAX_IMAGE_HEIGHT}")
        
        output = io.BytesIO()
        
        # Determine output format
        if has_transparency:
            # Keep PNG for transparency
            img.save(output, format='PNG', optimize=True)
            ext = 'png'
            content_type = 'image/png'
            current_app.logger.info("Keeping PNG format (has transparency)")
        else:
            # Convert to JPEG for better compression
            if img.mode in ('RGBA', 'LA', 'P'):
                img = img.convert('RGB')
            img.save(output, format='JPEG', quality=JPEG_QUALITY, optimize=True)
            ext = 'jpg'
            content_type = 'image/jpeg'
            current_app.logger.info("Converting to JPEG (no transparency)")
        
        output.seek(0)
        
        # Generate new filename with correct extension
        base_name = original_filename.rsplit('.', 1)[0] if '.' in original_filename else original_filename
        new_filename = f"{base_name}.{ext}"
        
        current_app.logger.info(f"Image compressed: {original_filename} -> {new_filename} ({output.getbuffer().nbytes} bytes)")
        
        return output, new_filename, content_type
        
    except Exception as e:
        current_app.logger.error(f"Image compression error: {e}")
        # Return original if compression fails
        file_stream.seek(0)
        ext = original_filename.rsplit('.', 1)[1].lower() if '.' in original_filename else 'png'
        content_type = f"image/{ext}" if ext in ['png', 'jpg', 'jpeg', 'gif'] else 'application/octet-stream'
        return file_stream, original_filename, content_type


# Singleton instance
_s3_service_instance = None

def get_storage_service():
    global _s3_service_instance
    if _s3_service_instance is None:
        _s3_service_instance = S3Service()
    return _s3_service_instance
