import requests
from django.http import JsonResponse
from dotenv import load_dotenv
from django.views.decorators.csrf import csrf_exempt
import hashlib
import mysql.connector
import json
import os
from openai import OpenAI
from pinecone import Pinecone, ServerlessSpec

# Load environment variables from .env file
load_dotenv()
client = OpenAI()
pc = Pinecone(api_key=os.getenv("PINECONE_API_KEY"))

@csrf_exempt
def register(request):

    if request.method == 'POST':
        conn = None
        cursor = None
        try:
            data = json.loads(request.body)
            username = data['username']
            password = data['password']
            email = data['email']

            # Encrypting the password

            # Create a SHA3-256 hash object
            sha3_256_hasher = hashlib.sha3_256()

            # Update the hash object with the data (encoded to bytes)
            sha3_256_hasher.update(password.encode('utf-8'))

            # Get the hexadecimal digest
            encrypted_password = sha3_256_hasher.hexdigest()

            # topic_weights
            topic_weights = json.dumps({
                "AI & Machine Learning": 0,
                "Software Development": 0,
                "Cybersecurity": 0,
                "Startups": 0,
                "Marketing": 0,
                "Finance": 0,
                "Fitness": 0,
                "Nutrition": 0,
                "Mental Health": 0,
                "Education": 0,
                "Social Issues": 0,
                "Entertainment": 0,
                "Art": 0,
                "Writing": 0,
                "Music": 0
            })

            # Creating embedding for the topic preferences
            response = client.embeddings.create(
                model="text-embedding-3-small",
                input=topic_weights
            )

            # Creating article index on pinecone
            user_index = pc.Index("user-embeddings")

            # Storing embedding of vectors for the topic preferences onto pinecone
            vector = {
                "id": username,
                "values": response.data[0].embedding
            }

            user_index.upsert(vectors=[vector])

            conn = mysql.connector.connect(
                host=os.getenv("DB_HOST"),
                user=os.getenv("DB_USER"),
                password=os.getenv("DB_PASSWORD"),
                database=os.getenv("DB_DATABASE")
            )

            # Registering and adding the registration details to the mysql user table
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO user (username, email, hashed_password, topic_weights, topic_preferences) VALUES (%s, %s, %s, %s, %s)",
                (username, email, encrypted_password, topic_weights, json.dumps([]))
            )  
            conn.commit()
            return JsonResponse({'message': 'User registered successfully', 'topic_weights': topic_weights})

        except mysql.connector.Error as err:
            errors = {}
            if err.errno == 1062:
                # Duplicate entry — check which field is duplicated
                if "user.username" in str(err):
                    errors['username'] = 'Username already exists'
                if "user.email" in str(err):
                    errors['email'] = 'Email already exists'
                if errors:
                    return JsonResponse({'errors': errors}, status=400)
            return JsonResponse({'error': err.msg}, status=500)   
        finally: 
            if cursor:
                cursor.close()
            if conn:
                conn.close()

    return JsonResponse({'error': 'Only POST method allowed'}, status=405)

@csrf_exempt
def update_user(request):

    if request.method == 'PATCH':
        conn = None
        cursor = None
        try:
            data = json.loads(request.body)
            username = data['username']
            email = data['email']
            password = data['password']
            topic_weights = json.dumps(data['topic_weights'])
            topic_preferences = json.dumps(data['topic_preferences'])

            # Encrypting the password

            # Create a SHA3-256 hash object
            sha3_256_hasher = hashlib.sha3_256()

            # Update the hash object with the data (encoded to bytes)
            sha3_256_hasher.update(password.encode('utf-8'))

            # Get the hexadecimal digest
            encrypted_password = sha3_256_hasher.hexdigest()

            # Creating embedding for the topic preferences
            response = client.embeddings.create(
                model="text-embedding-3-small",
                input=topic_weights
            )

            # Creating article index on pinecone
            user_index = pc.Index("user-embeddings")

            # Storing embedding of vectors for the topic preferences onto pinecone
            vector = {
                "id": username,
                "values": response.data[0].embedding
            }

            user_index.upsert(vectors=[vector])

            conn = mysql.connector.connect(
                host=os.getenv("DB_HOST"),
                user=os.getenv("DB_USER"),
                password=os.getenv("DB_PASSWORD"),
                database=os.getenv("DB_DATABASE")
            )

            # Registering and adding the registration details to the mysql user table
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE user SET topic_weights = %s, topic_preferences = %s, email = %s, hashed_password = %sWHERE username = %s",
                (topic_weights, topic_preferences, email, encrypted_password, username)
            )  

            conn.commit()
        except mysql.connector.Error as err:
            errors = {}
            if err.errno == 1062:
                # Duplicate entry — check which field is duplicated
                if "user.email" in str(err):
                    errors['email'] = 'Email already exists'
                if errors:
                    return JsonResponse({'errors': errors}, status=400)
            return JsonResponse({'error': err.msg}, status=500)   
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
        return JsonResponse({'message': 'User onboarded successfully'})

    return JsonResponse({'error': 'Only POST method allowed'}, status=405)

@csrf_exempt
def login(request):
    if request.method == 'POST':
        conn = None
        cursor = None
        try:
            data = json.loads(request.body)
            username = data['username']
            password = data['password']

            # Encrypting the password

            # Create a SHA3-256 hash object
            sha3_256_hasher = hashlib.sha3_256()

            # Update the hash object with the data (encoded to bytes)
            sha3_256_hasher.update(password.encode('utf-8'))

            # Get the hexadecimal digest
            encrypted_password = sha3_256_hasher.hexdigest()
            conn = mysql.connector.connect(
                host=os.getenv("DB_HOST"),
                user=os.getenv("DB_USER"),
                password=os.getenv("DB_PASSWORD"),
                database=os.getenv("DB_DATABASE")
            )

            # Registering and adding the registration details to the mysql user table
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM user WHERE username = %s AND hashed_password = %s",
                (username, encrypted_password)
            )  
            result = cursor.fetchone()
            if result:
                return JsonResponse({'success': True, 'topic_weights': result[4], 'email': result[2], 'topic_preferences': result[7]})
            return JsonResponse({'success': False})

        except mysql.connector.Error as err:
            errors = {}
            if err.errno == 1062:
                # Duplicate entry — check which field is duplicated
                if "user.email" in str(err):
                    errors['email'] = 'Email already exists'
                if errors:
                    return JsonResponse({'errors': errors}, status=400)
            return JsonResponse({'error': err.msg}, status=500)   
        finally: 
            if cursor:
                cursor.close()
            if conn:
                conn.close()

    return JsonResponse({'error': 'Only POST method allowed'}, status=405)