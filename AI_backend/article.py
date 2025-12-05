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
from datetime import datetime

# Load environment variables from .env file
load_dotenv()
client = OpenAI()
pc = Pinecone(api_key=os.getenv("PINECONE_API_KEY"))

@csrf_exempt
def add_article(request):

    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            title = data['title']
            content = data['content']
            topics = data['topics']
            author = data['username']

            topic_weights = {
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
            }

            for topic in topics:
                if topic in topic_weights:
                    topic_weights[topic] = 1

            # Creating embedding for the topic preferences
            response = client.embeddings.create(
                model="text-embedding-3-small",
                input=json.dumps(topic_weights)
            )
            embedding_values = response.data[0].embedding
            conn = mysql.connector.connect(
                host=os.getenv("DB_HOST"),
                user=os.getenv("DB_USER"),
                password=os.getenv("DB_PASSWORD"),
                database=os.getenv("DB_DATABASE")
            )
        
            cursor = conn.cursor()
            
            # Insert into article table
            query = "INSERT INTO article (title, content, topics, author, created_at, like_count, dislike_count) VALUES (%s, %s, %s, %s, %s, %s, %s)"
            values = (title, content, json.dumps(topics), author, datetime.now(), 0, 0)
            cursor.execute(query, values)
            article_id = cursor.lastrowid

            # Creating article index on pinecone
            article_index = pc.Index("article-embeddings")
            # Storing embedding on pinecone with article_id
            vector = {
                "id": str(article_id),
                "values": embedding_values
            }
            article_index.upsert(vectors=[vector])

            conn.commit()
            return JsonResponse({'message': 'Article added successfully', 'id': article_id})

        except mysql.connector.Error as err:
            return JsonResponse({'error': err.msg}, status=500)
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)
        finally:
            if 'cursor' in locals() and cursor:
                cursor.close()
            if 'conn' in locals() and conn:
                conn.close()

    return JsonResponse({'error': 'Only POST method allowed'}, status=405)