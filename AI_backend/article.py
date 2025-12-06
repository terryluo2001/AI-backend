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

            article = json.dumps({
                "title": title,
                "content": content
            })

            # Creating embedding for the topic preferences
            response = client.embeddings.create(
                model="text-embedding-3-small",
                input=json.dumps(article)
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

            answer_text = ""
            prompt = f"Generate a short, clear question for a user based on this article:\nTitle: {title}\nContent: {content}\nQuestion:"
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "user", "content": prompt}
                ]
            )
            question_text = response.choices[0].message.content
            # Insert into article table
            query = "INSERT INTO user_answers (username, article_id, question_text, answer_text, created_at) VALUES (%s, %s, %s, %s, %s)"
            values = (author, article_id, question_text, answer_text, datetime.now())
            cursor.execute(query, values)

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

@csrf_exempt
def get_article(request):
    if request.method == 'GET':
        conn = None
        cursor = None
        try:
            username = request.GET.get('username')
            if not username:
                return JsonResponse({'error': 'Username is required'}, status=400)

            # 1. Fetch user's embedding from Pinecone directly
            user_index = pc.Index("user-embeddings")
            fetch_response = user_index.fetch(ids=[username])

            if not fetch_response or username not in fetch_response.vectors:
                return JsonResponse({'error': 'User embedding not found'}, status=404)

            embedding_values = ((fetch_response.vectors)[username]).values 

            # 2. Query Pinecone for relevant articles
            article_index = pc.Index("article-embeddings")
            search_results = article_index.query(
                vector=embedding_values,
                top_k=10, # Adjustable
                include_values=False
            )
            
            article_ids = [match['id'] for match in search_results['matches']]
            
            if not article_ids:
                return JsonResponse({'articles': []})

            # 3. Fetch full article details from MySQL
            conn = mysql.connector.connect(
                host=os.getenv("DB_HOST"),
                user=os.getenv("DB_USER"),
                password=os.getenv("DB_PASSWORD"),
                database=os.getenv("DB_DATABASE")
            )
            cursor = conn.cursor()

            # Create a localized format string for the IN clause
            format_strings = ','.join(['%s'] * len(article_ids))
            
            # We want to fetch articles in the order of relevance (Pinecone result order)
            # MySQL FIELD() function can help preserve order, or we can resort in Python.
            # For simplicity, let's fetch and resort in Python.
            
            query = f"""SELECT article.id, article.title, article.content, article.topics, 
            article.author, article.created_at, article.like_count, article.dislike_count, 
            user_answers.question_text, user_answers.answer_text, user_answers.id FROM article 
            INNER JOIN user_answers ON article.id = user_answers.article_id WHERE article.id IN ({format_strings})"""
            cursor.execute(query, tuple(article_ids))
            
            articles_db = cursor.fetchall()

            # Convert to list of dicts
            articles_map = {}
            for row in articles_db:
                articles_map[str(row[0])] = {
                    'id': row[0],
                    'title': row[1],
                    'snippet': row[2][:100] + '...',
                    'content': row[2],
                    'topics': json.loads(row[3]) if row[3] else [],
                    'author': row[4],
                    'createdAt': row[5],
                    'likes': row[6],
                    'dislikes': row[7],
                    'questions': {'id': row[10], 'text': row[8]},
                    'answers': {'id': row[10], 'text': row[9]}
                }

            query = f"SELECT * FROM interactionEvent WHERE article_id IN ({format_strings}) AND username = %s"
            params = tuple(article_ids) + (username,)
            cursor.execute(query, params)
            interaction_events = cursor.fetchall()

            interaction_dict = {row[2]: row[3] for row in interaction_events} 
            print(articles_map)
            for interaction in interaction_dict:
                print(str(interaction))
                print(articles_map[str(interaction)])
                articles_map[str(interaction)]['userAction'] = "like" if interaction_dict[interaction] == 1 else "dislike"
                print("Wrong")
            # Reconstruct list in order of Pinecone results
            ordered_articles = []
            for art_id in article_ids:
                if art_id in articles_map:
                    ordered_articles.append(articles_map[art_id])

            return JsonResponse({'articles': ordered_articles})

        except mysql.connector.Error as err:
            return JsonResponse({'error': err.msg}, status=500)
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()

    return JsonResponse({'error': 'Only GET method allowed'}, status=405)

@csrf_exempt
def toggle_like(request, article_id): 
    if request.method == 'POST':
        conn = None
        cursor = None
        
        conn = mysql.connector.connect(
            host=os.getenv("DB_HOST"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"),
            database=os.getenv("DB_DATABASE")
        )
        cursor = conn.cursor()

        data = json.loads(request.body)
        username = data['username']
        if 'userAction' not in data:
            
            query = "UPDATE article SET like_count = like_count + 1 WHERE id = %s"
            cursor.execute(query, (article_id,))
            query = "INSERT INTO interactionEvent (username, article_id, value) VALUES (%s, %s, %s)"
            cursor.execute(query, (username, article_id, 1))

        elif data['userAction'] == 'dislike':
            
            query = "UPDATE article SET dislike_count = dislike_count - 1, like_count = like_count + 1 WHERE id = %s"
            cursor.execute(query, (article_id,))
            query = "INSERT INTO interactionEvent (username, article_id, value) VALUES (%s, %s, %s)"
            cursor.execute(query, (username, article_id, 1))

        
        elif data['userAction'] == 'like':
            query = "UPDATE article SET like_count = like_count - 1 WHERE id = %s"
            cursor.execute(query, (article_id,))
            query = "DELETE FROM interactionEvent WHERE article_id = %s AND username = %s"
            cursor.execute(query, (article_id, username))

        query = "SELECT topic_weights FROM user WHERE username = %s"
        cursor.execute(query, (username,))
        topic_weights = json.loads(cursor.fetchone()[0])
        
        query = "SELECT topics FROM article WHERE id = %s"
        cursor.execute(query, (article_id,))
        topics = json.loads(cursor.fetchone()[0])
        for topic in topics:
            if topic in topic_weights:
                topic_weights[topic] += 1
        
        query = "UPDATE user SET topic_weights = %s WHERE username = %s"
        cursor.execute(query, (json.dumps(topic_weights), username))

        # Creating embedding for the topic preferences
        response = client.embeddings.create(
            model="text-embedding-3-small",
            input=json.dumps(topic_weights)
        )

        # Pinecone, adding vector inside user embeddings with the newly created embedding vector
        user_index = pc.Index("user-embeddings")
        vector = {  
            "id": username,
            "values": response.data[0].embedding
        }
        user_index.upsert(
            vectors=[vector]
        )
        conn.commit()
        return JsonResponse({'message': 'Like added successfully'}, status=200)

    return JsonResponse({'error': 'Only GET method allowed'}, status=405)

@csrf_exempt
def toggle_dislike(request, article_id): 
    if request.method == 'POST':
        conn = None
        cursor = None
        
        conn = mysql.connector.connect(
            host=os.getenv("DB_HOST"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"),
            database=os.getenv("DB_DATABASE")
        )
        cursor = conn.cursor()

        data = json.loads(request.body)
        username = data['username']
        if 'userAction' not in data:
            
            query = "UPDATE article SET dislike_count = dislike_count + 1 WHERE id = %s"
            cursor.execute(query, (article_id,))
            query = "INSERT INTO interactionEvent (username, article_id, value) VALUES (%s, %s, %s)"
            cursor.execute(query, (username, article_id, -1))

        elif data['userAction'] == 'dislike':
            
            query = "UPDATE article SET dislike_count = dislike_count - 1 WHERE id = %s"
            cursor.execute(query, (article_id,))
            query = "DELETE FROM interactionEvent WHERE article_id = %s AND username = %s"
            cursor.execute(query, (article_id, username))
        
        elif data['userAction'] == 'like':
            query = "UPDATE article SET like_count = like_count - 1, dislike_count = dislike_count + 1 WHERE id = %s"
            cursor.execute(query, (article_id,))
            query = "INSERT INTO interactionEvent (username, article_id, value) VALUES (%s, %s, %s)"
            cursor.execute(query, (username, article_id, -1))

        query = "SELECT topic_weights FROM user WHERE username = %s"
        cursor.execute(query, (username,))
        topic_weights = json.loads(cursor.fetchone()[0])
        
        query = "SELECT topics FROM article WHERE id = %s"
        cursor.execute(query, (article_id,))
        topics = json.loads(cursor.fetchone()[0])
        for topic in topics:
            if topic in topic_weights:
                topic_weights[topic] -= 1
        
        query = "UPDATE user SET topic_weights = %s WHERE username = %s"
        cursor.execute(query, (json.dumps(topic_weights), username))

        # Creating embedding for the topic preferences
        response = client.embeddings.create(
            model="text-embedding-3-small",
            input=json.dumps(topic_weights)
        )

        # Pinecone, adding vector inside user embeddings with the newly created embedding vector
        user_index = pc.Index("user-embeddings")
        vector = {  
            "id": username,
            "values": response.data[0].embedding
        }
        user_index.upsert(
            vectors=[vector]
        )

        conn.commit()
        return JsonResponse({'message': 'Dislike added successfully'}, status=200)

    return JsonResponse({'error': 'Only POST method allowed'}, status=405)

@csrf_exempt
def answer(request): 
    if request.method == 'POST':
        conn = None
        cursor = None
        
        conn = mysql.connector.connect(
            host=os.getenv("DB_HOST"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"),
            database=os.getenv("DB_DATABASE")
        )
        cursor = conn.cursor()

        data = json.loads(request.body)
        answers = data['answers']
        print(answers)
        query = "UPDATE user_answers SET answer_text = %s WHERE id = %s"
        cursor.execute(query, (answers['text'], int(answers['id'])))
        conn.commit()
        return JsonResponse({'message': 'Answer successfully'}, status=200)

    return JsonResponse({'error': 'Only POST method allowed'}, status=405)