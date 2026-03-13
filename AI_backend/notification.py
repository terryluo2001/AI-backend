import requests
from django.http import JsonResponse
from dotenv import load_dotenv
from django.views.decorators.csrf import csrf_exempt
import mysql.connector
import json
import os
from datetime import datetime

# Load environment variables from .env file
load_dotenv()

@csrf_exempt
def notification(request, username):
    if request.method == 'GET':
        conn = None
        cursor = None
        try:

            conn = mysql.connector.connect(
                host=os.getenv('DB_HOST'),
                user=os.getenv('DB_USER'),
                password=os.getenv('DB_PASSWORD'),
                database=os.getenv('DB_DATABASE')
            )
            cursor = conn.cursor()
            cursor.execute("""SELECT notification.id, notification.time, notification.article_id, notification.author, article.title, comment.text FROM notification 
            INNER JOIN article ON notification.article_id = article.id
            INNER JOIN comment ON comment.id = notification.comment_id
            WHERE article.author = %s AND notification.author != article.author ORDER BY notification.time DESC""", (username,))
            notifications = cursor.fetchall()
            returned_notifications = []
            for notification in notifications:
                comment_text = notification[5]
                if len(comment_text) > 50:
                    comment_text = comment_text[:47] + "..."
                returned_notifications.append({
                    "id": notification[0],
                    "time": notification[1],
                    "message": f"{notification[3]} commented on '{notification[4]}': \"{comment_text}\"",
                    "articleId": notification[2],
                    "author": notification[3],
                    "title": notification[4],
                    "text": notification[5]
                })
            return JsonResponse({"notifications": returned_notifications})
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()

    return JsonResponse({'error': 'Only GET method allowed'}, status=405)
    
@csrf_exempt
def delete_notification(request, id):
    if request.method == 'DELETE':
        conn = None
        cursor = None
        try:
            conn = mysql.connector.connect(
                host=os.getenv('DB_HOST'),
                user=os.getenv('DB_USER'),
                password=os.getenv('DB_PASSWORD'),
                database=os.getenv('DB_DATABASE')
            )
            cursor = conn.cursor()
            cursor.execute("DELETE FROM notification WHERE id = %s", (id,))
            conn.commit()
            
            return JsonResponse({"message": "Successfully deleted notifications"})
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()

    return JsonResponse({'error': 'Only DELETE method allowed'}, status=405)
    