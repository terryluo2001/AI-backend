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
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync


@csrf_exempt
def add_article(request):

    if request.method == 'POST':
        conn = None
        cursor = None
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
            # Storing embedding on pinecone with article_id and author metadata
            vector = {
                "id": str(article_id),
                "values": embedding_values,
                "metadata": {"author": author}
            }
            article_index.upsert(vectors=[vector])

            conn.commit()

            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                "articles",
                {
                    "type": "new_article",
                    "article": {
                        "id": article_id,
                        "title": title,
                        "content": content,
                        'snippet': content[:100] + '...',
                        "topics": topics,
                        "author": author,
                        "createdAt": str(datetime.now()),
                        "likes": 0,
                        "dislikes": 0
                    }
                }
            )
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
def get_recommended_article(request):
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

            # 2. Query Pinecone for relevant articles (excluding user's own articles)
            article_index = pc.Index("article-embeddings")
            search_results = article_index.query(
                vector=embedding_values,
                top_k=10, # Adjustable
                filter={"author": {"$ne": username}},
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
            INNER JOIN user_answers ON article.id = user_answers.article_id 
            WHERE article.id IN ({format_strings})"""
            cursor.execute(query, tuple(article_ids))
            articles_db = cursor.fetchall()

            # Convert to list of dicts
            articles_map = {}
            for row in articles_db:
                query = """SELECT comment.id, comment.text, comment.article_id, comment.author, comment.created_at
                FROM comment WHERE comment.article_id = %s ORDER BY comment.created_at DESC"""
                cursor.execute(query, (row[0],))
                comments = cursor.fetchall()
                article_comments = []
                for comment in comments:
                    article_comments.append({
                        'id': comment[0],
                        'text': comment[1],
                        'author': comment[3],
                        'createdAt': comment[4]
                    })
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
                    'answers': {'id': row[10], 'text': row[9]},
                    'comments': article_comments
                }

            query = f"SELECT * FROM interactionEvent WHERE article_id IN ({format_strings}) AND username = %s"
            params = tuple(article_ids) + (username,)
            cursor.execute(query, params)
            interaction_events = cursor.fetchall()

            interaction_dict = {row[2]: row[3] for row in interaction_events} 
            for interaction in interaction_dict:
                articles_map[str(interaction)]['userAction'] = "like" if interaction_dict[interaction] == 1 else "dislike"
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
def get_articles(request):
    if request.method == 'GET':
        conn = None
        cursor = None
        try:

            username = request.GET.get('username')
            if not username:
                return JsonResponse({'error': 'Username is required'}, status=400)

            # 3. Fetch full article details from MySQL
            conn = mysql.connector.connect(
                host=os.getenv("DB_HOST"),
                user=os.getenv("DB_USER"),
                password=os.getenv("DB_PASSWORD"),
                database=os.getenv("DB_DATABASE")
            )
            cursor = conn.cursor()
            
            query = """SELECT article.id, article.title, article.content, article.topics, 
            article.author, article.created_at, article.like_count, article.dislike_count, 
            user_answers.question_text, user_answers.answer_text, user_answers.id FROM article 
            INNER JOIN user_answers ON article.id = user_answers.article_id"""
            cursor.execute(query)
            articles_db = cursor.fetchall()

            # Convert to list of dicts
            articles_map = {}
            for row in articles_db:
                query = """SELECT comment.id, comment.text, comment.article_id, comment.author, comment.created_at
                FROM comment WHERE comment.article_id = %s ORDER BY comment.created_at DESC"""
                cursor.execute(query, (row[0],))
                comments = cursor.fetchall()
                article_comments = []
                for comment in comments:
                    article_comments.append({
                        'id': comment[0],
                        'text': comment[1],
                        'author': comment[3],
                        'createdAt': comment[4]
                    })
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
                    'answers': {'id': row[10], 'text': row[9]},
                    'comments': article_comments
                }
            
            query = f"SELECT * FROM interactionEvent WHERE username = %s"
            params = (username,)
            cursor.execute(query, params)
            interaction_events = cursor.fetchall()
            interaction_dict = {row[2]: row[3] for row in interaction_events} 
            for interaction in interaction_dict:
                articles_map[str(interaction)]['userAction'] = "like" if interaction_dict[interaction] == 1 else "dislike"

            # Reconstruct list in order of Pinecone results
            ordered_articles = []
            for article in articles_map:
                ordered_articles.append(articles_map[article])
            
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
                if 'userAction' not in data:
                    topic_weights[topic] += 1
                elif data['userAction'] == 'dislike':
                    topic_weights[topic] += 1
                elif data['userAction'] == 'like':
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

        # Fetch updated counts
        cursor.execute("SELECT like_count, dislike_count FROM article WHERE id = %s", (article_id,))
        counts = cursor.fetchone()
        conn.commit()
        
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            "articles",
            {
                "type": "article_update",
                "action": "like",
                "article_id": article_id,
                "likes": counts[0],
                "dislikes": counts[1]
            }
        )
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
                if 'userAction' not in data:
                    topic_weights[topic] -= 1
                elif data['userAction'] == 'dislike':
                    topic_weights[topic] += 1
                elif data['userAction'] == 'like':
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

        channel_layer = get_channel_layer()
        
        # Fetch updated counts
        cursor.execute("SELECT like_count, dislike_count FROM article WHERE id = %s", (article_id,))
        counts = cursor.fetchone()

        async_to_sync(channel_layer.group_send)(
            "articles",
            {
                "type": "article_update",
                "action": "dislike",
                "article_id": article_id,
                "likes": counts[0],
                "dislikes": counts[1]
            }
        )

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
        query = "SELECT is_answered from user_answers WHERE id = %s"
        cursor.execute(query, (int(answers['id']),))
        result = cursor.fetchone()
        
        query = "UPDATE user_answers SET answer_text = %s, is_answered = %s WHERE id = %s"
        cursor.execute(query, (answers['text'], True, int(answers['id'])))

        if result[0]:          
            conn.commit()
            return JsonResponse({'error': 'Answer already given'}, status=400)
        
        query = "SELECT * FROM user_answers WHERE id = %s"
        cursor.execute(query, (int(answers['id']),))
        result = cursor.fetchone()

        username = result[1]
        article_id = result[2]
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
        conn.commit()
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            "articles",
            {
                "type": "article_update",
                "action": "answer",
                "article_id": article_id
            }
        )
        return JsonResponse({'message': 'Answer successfully'}, status=200)

    return JsonResponse({'error': 'Only POST method allowed'}, status=405)

@csrf_exempt
def comment(request): 
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
        article_id = data['article_id']
        text = data['text']
        author = data['author']
        query = "INSERT INTO comment (text, article_id, author, created_at) VALUES (%s, %s, %s, %s)"
        cursor.execute(query, (text, article_id, author, datetime.now()))
        comment_id = cursor.lastrowid

        query = "INSERT INTO notification (article_id, author, time, comment_id) VALUES (%s, %s, %s, %s)"
        cursor.execute(query, (article_id, author, datetime.now(), comment_id))
        notification_id = cursor.lastrowid
        conn.commit()

        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            "articles",
            {
                "type": "article_update",
                "action": "comment",
                "article_id": article_id,
                "comment": {
                     'id': comment_id, 'author': author, 'text': text, 'createdAt': str(datetime.now())
                }
            }
        )

        cursor.execute("SELECT author, title FROM article WHERE id = %s", (article_id,))
        article_row = cursor.fetchone()
        if article_row:
             article_author = article_row[0]
             article_title = article_row[1]
             if article_author != author: # don't notify self
                 async_to_sync(channel_layer.group_send)(
                    f"user_{article_author}",
                    {
                        "type": "new_notification",
                        "notification": {
                             "id": notification_id,
                             "message": f"{author} commented on '{article_title}': \"{text[:47] + '...' if len(text) > 50 else text}\"",
                             "articleId": article_id,
                             "author": author,
                             "time": str(datetime.now())
                        }
                    }
                 )

        return JsonResponse({'message': 'Answer successfully', 'id': comment_id, 'author': author, 'text': text, 'created_at': datetime.now()}, status=200)

    return JsonResponse({'error': 'Only POST method allowed'}, status=405)