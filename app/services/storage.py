import boto3
from botocore.exceptions import ClientError
from flask import current_app
from app.config import Config

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
            return full_key
        except ClientError as e:
            current_app.logger.error(f"S3 Upload Error: {e}")
            return None

    def delete_file(self, filename):
        """
        Deletes a file from B2.
        :param filename: Full key of the file to delete
        """
        try:
            self.s3_client.delete_object(Bucket=self.bucket_name, Key=filename)
            return True
        except ClientError as e:
            current_app.logger.error(f"S3 Delete Error: {e}")
            return False

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

# Singleton instance
_s3_service_instance = None

def get_storage_service():
    global _s3_service_instance
    if _s3_service_instance is None:
        _s3_service_instance = S3Service()
    return _s3_service_instance
