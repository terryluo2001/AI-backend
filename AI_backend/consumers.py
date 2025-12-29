from channels.generic.websocket import AsyncWebsocketConsumer
import json

class ArticleConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.group_name = "articles"
        await self.channel_layer.group_add(
            self.group_name,
            self.channel_name
        )
        await self.accept()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(
            self.group_name,
            self.channel_name
        )

    # Receive message from room group
    async def article_update(self, event):
        # Send message to WebSocket
        await self.send(text_data=json.dumps(event))

    async def new_article(self, event):
        await self.send(text_data=json.dumps(event))


class NotificationConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.username = self.scope['url_route']['kwargs']['username']
        self.group_name = f"user_{self.username}"

        await self.channel_layer.group_add(
            self.group_name,
            self.channel_name
        )
        await self.accept()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(
            self.group_name,
            self.channel_name
        )

    async def new_notification(self, event):
        await self.send(text_data=json.dumps(event))
