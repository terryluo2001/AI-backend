# Use official Python slim image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Prevent Python from writing .pyc files and buffer stdout
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Install system dependencies for Python packages
RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --upgrade pip
RUN pip install -r requirements.txt

# Copy project files
COPY . .

# Collect static files for production
RUN python manage.py collectstatic --noinput

# Expose port for container
EXPOSE 8000

# Run Django with Gunicorn
CMD ["gunicorn", "AI_backend.wsgi:application", "--bind", "0.0.0.0:8000"]
