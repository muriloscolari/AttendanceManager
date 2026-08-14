FROM python:3.10-slim

WORKDIR /app

# Install system dependencies required for PostgreSQL and building Python packages
RUN apt-get update && apt-get install -y \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install them
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

# Copy the rest of the application
COPY . .

# Expose port 5000
EXPOSE 5000

# Run migrations and start the application using Gunicorn for better performance
CMD ["sh", "-c", "flask db upgrade && gunicorn -b 0.0.0.0:5000 'app:create_app()'"]
