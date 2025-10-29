import asyncio
import json
import logging
import aiohttp
import time
import mimetypes
from typing import Dict, List, Optional, Callable, Any, Union, BinaryIO, Tuple, Awaitable
import os
from .room import Room
from .user import User
from .message import Message
from .events import Event, MessageEvent, ReactionEvent, event_factory
from .context import CommandContext
from .exceptions import (
    MatrixError, MatrixConnectionError, 
    MatrixAuthError, MatrixAPIError, ForbiddenError, NotFoundError,
    MatrixRoomError, MatrixEventError
)

logger = logging.getLogger(__name__)

class Client:
    """Main client for interacting with the Matrix API."""
    
    def __init__(self, homeserver: str, access_token: str = None, user_id: str = None, command_prefix: str = '!'):
        """Initialize the Matrix client.
        
        Args:
            homeserver: URL of the Matrix server (ex: 'https://matrix.org')
            access_token: OAuth access token (optional)
            user_id: User ID (optional)
            command_prefix: Command prefix (default '!')
        """
        self.homeserver = homeserver.rstrip('/')
        self.access_token = access_token
        self.user_id = user_id
        self.command_prefix = command_prefix
        self._commands = {}
        self._session = None
        self._sync_token = None
        self._event_handlers = {
            'on_ready': [],
            'on_message': [],
            'on_command': [],
            'on_reaction': [],
            'on_reaction_deleted': [],
            'on_room_join': [],
            'on_room_leave': [],
        }
        self.rooms = {}  # room_id -> Room
        self.users = {}  # user_id -> User
        self._ready_handlers = []  # on_ready handlers
        self._start_time = None  # Client startup timestamp
        self._first_sync_complete = False  # Indicates if the first sync is complete
        self._sync_task = None  # Task for the sync loop
        
        # Event handlers
        self.event_handlers = {
            'on_message': [],
            'on_reaction': [],
            'on_reaction_deleted': [],
            'on_room_join': [],
            'on_room_leave': [],
        }
        logger.debug("Client initialized successfully")
    
    # ===== User Management =====
    
    async def get_profile(self, user_id: str) -> dict:
        """Get user profile information.
        
        Args:
            user_id: The full user ID (ex: @user:example.com)
            
        Returns:
            dict: User profile information (displayname, avatar_url, etc.)
        """
        try:
            data = await self._api_request(
                'GET',
                f'/profile/{user_id}'
            )
            return data
        except Exception as e:
            logger.warning(f"Failed to retrieve profile for {user_id}: {e}")
            return {}
    
    async def get_user(self, user_id: str) -> 'User':
        """Get a user object by ID.
        
        Args:
            user_id: The full user ID (e.g., @user:example.com)
            
        Returns:
            User: The user object corresponding to the ID
            
        Example:
            user = await client.get_user("@user:example.com")
            avatar = await user.get_avatar()
            if avatar:
                data = await avatar.read()
        """
        if user_id not in self.users:
            user = User(self, user_id)
            # Load profile asynchronously
            await user._ensure_profile_loaded()
            self.users[user_id] = user
        return self.users[user_id]
    
    # ===== Basic Methods =====
    
    async def __aenter__(self):
        await self.start()
        return self
    
    async def __aexit__(self, exc_type, exc, tb):
        await self.close()
    
    async def start(self):
        """Initialize connection to the Matrix server."""
        if self._session is None:
            self._session = aiohttp.ClientSession()
        
        # Record the startup time (in milliseconds)
        self._start_time = int(time.time() * 1000)
        logger.debug(f"Client starting at {self._start_time} (ms since epoch)")
        
        if self.access_token:
            try:
                whoami = await self._api_get('/_matrix/client/r0/account/whoami')
                self.user_id = whoami.get('user_id')
                self.device_id = whoami.get('device_id')
                logger.info(f"Successfully connected as {self.user_id}")
                logger.debug(f"Startup timestamp set to: {self._start_time}")
                
                self._sync_token = None
                logger.debug("Sync token not initialized, performing first sync...")
                
                self._first_sync_complete = False
                
            except MatrixAPIError as e:
                raise MatrixAuthError("Authentication failed with provided token") from e
    
    async def close(self) -> None:
        """Close the connection to the Matrix server."""
        if self._session and not self._session.closed:
            await self._session.close()
            
    async def run(self, username: str = None, password: str = None) -> None:
        """
        Start the client and manage the main event loop.
        
        Args:
            username: Matrix username (optional if already connected)
            password: Matrix password (optional if already connected)
        """
        try:
            if self._session is None or self._session.closed:
                logger.debug("Initializing HTTP session")
                self._session = aiohttp.ClientSession()
            
            if username and password:
                logger.info(f"Logging in as {username}...")
                await self.login(username, password)
                logger.info("Successfully logged in")
            
            logger.info("Loading joined rooms...")
            rooms_loaded = await self._load_joined_rooms()
            
            if not rooms_loaded:
                logger.warning("Failed to load rooms, retrying...")
                await asyncio.sleep(1)
                rooms_loaded = await self._load_joined_rooms()
                
                if not rooms_loaded:
                    logger.error("Failed to load rooms after retry, continuing anyway...")
            
            logger.info("Starting sync loop...")
            await self.start_sync()
            
            max_attempts = 30
            attempts = 0
            
            logger.info("Waiting for sync to be ready...")
            
            while not getattr(self, '_sync_token', None) and self._should_sync and attempts < max_attempts:
                logger.debug(f"Waiting for sync token... (attempt {attempts + 1}/{max_attempts})")
                await asyncio.sleep(0.5)
                attempts += 1
            
            if not getattr(self, '_sync_token', None):
                logger.warning("No sync token received, real-time updates may be limited")
            else:
                logger.info(f"Sync token received: {self._sync_token[:10]}...")
            
            self._first_sync_complete = True
            logger.info(f"Initial sync complete - {len(self.rooms)} rooms loaded")
            
            if self.rooms:
                logger.info("Rooms loaded:")
                for i, room_id in enumerate(self.rooms.keys(), 1):
                    logger.info(f"  {i}. {room_id}")
            
            print("\n=== Bot started successfully! ===")
            print(f"Connected as: {self.user_id}")
            print("Listening for events... (Press Ctrl+C to quit)\n")
            
            # Call on_ready handlers
            if self._ready_handlers:
                logger.info(f"Calling {len(self._ready_handlers)} on_ready handlers...")
                for handler in self._ready_handlers:
                    try:
                        if asyncio.iscoroutinefunction(handler):
                            await handler()
                        else:
                            handler()
                    except Exception as e:
                        logger.error(f"Error in on_ready handler: {e}", exc_info=True)
            
            while self._should_sync:
                await asyncio.sleep(1)
                
                if (self._sync_task and self._sync_task.done() and 
                    not self._sync_task.cancelled() and self._should_sync):
                    logger.warning("Sync task has stopped unexpectedly, restarting...")
                    await asyncio.sleep(5)
                    await self.start_sync()
                
        except asyncio.CancelledError:
            logger.debug("Execution cancelled")
            
        except KeyboardInterrupt:
            print("\nStopping bot...")
            
        except Exception as e:
            logger.error(f"Error in main loop: {e}", exc_info=True)
            raise
            
        finally:
            self._should_sync = False
            if hasattr(self, '_sync_task') and self._sync_task:
                self._sync_task.cancel()
                try:
                    await self._sync_task
                except asyncio.CancelledError:
                    pass
            
            if self._session and not self._session.closed:
                await self._session.close()
                self._session = None
    
    # ===== Authentication =====
    
    async def login(self, username: str, password: str, device_name: str = "MatrixLib") -> None:
        """Authenticate the user with a username and password."""
        if self._session is None:
            self._session = aiohttp.ClientSession()
            
        login_data = {
            'type': 'm.login.password',
            'identifier': {
                'type': 'm.id.user',
                'user': username
            },
            'password': password,
            'initial_device_display_name': device_name
        }
        
        try:
            response = await self._api_post('/_matrix/client/r0/login', login_data, auth_required=False)
            self.access_token = response['access_token']
            self.user_id = response['user_id']
            self.device_id = response['device_id']
            
            if self._session:
                await self._session.close()
                
            self._session = aiohttp.ClientSession(headers={
                'Authorization': f'Bearer {self.access_token}'
            })
            
            logger.info(f"Successfully logged in as {self.user_id}")
            return True
            
        except MatrixAPIError as e:
            if self._session:
                await self._session.close()
                self._session = None
            raise MatrixAuthError(f"Authentication failed: {str(e)}") from e
    
    # ===== Room Management =====
    
    async def join_room(self, room_id_or_alias: str) -> Room:
        """Join a room by its ID or alias."""
        try:
            if room_id_or_alias.startswith('#'):
                room_id = await self._resolve_room_alias(room_id_or_alias)
            else:
                room_id = room_id_or_alias
            
            await self._api_post(f"/_matrix/client/r0/join/{room_id}")
            
            room_info = await self._api_get(f"/_matrix/client/r0/rooms/{room_id}/state/m.room.member/{self.user_id}")
            
            room = Room(room_id, self, **room_info)
            self.rooms[room_id] = room
            
            await self._dispatch_event('on_room_join', room)
            
            return room
            
        except MatrixAPIError as e:
            raise MatrixRoomError(f"Failed to join room {room_id_or_alias}") from e
    
    async def leave_room(self, room_id: str) -> None:
        """Leave a room."""
        try:
            await self._api_post(f"/_matrix/client/r0/rooms/{room_id}/leave")
            room = self.rooms.pop(room_id, None)
            if room:
                await self._dispatch_event('on_room_leave', room)
        except MatrixAPIError as e:
            raise MatrixRoomError(f"Failed to leave room {room_id}") from e
    
    # ===== Message Sending =====
    
    async def upload_file(
        self, 
        file_path: str, 
        content_type: Optional[str] = None,
        filename: Optional[str] = None
    ) -> str:
        """
        Upload a file to the content repository.
        
        Args:
            file_path: Path to the file to upload
            content_type: Content type of the file (optional, will be guessed if not provided)
            filename: Filename to use (optional, will use the basename of file_path if not provided)
            
        Returns:
            str: MXC URI of the uploaded file
        """
        
        if not os.path.isfile(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")
            
        if not filename:
            filename = os.path.basename(file_path)
            
        if not content_type:
            content_type, _ = mimetypes.guess_type(file_path)
            if not content_type:
                content_type = 'application/octet-stream'
        
        url = f"{self.homeserver}/_matrix/media/r0/upload"
        params = {'filename': filename}
        
        try:
            with open(file_path, 'rb') as f:
                headers = {
                    'Content-Type': content_type,
                    'Content-Length': str(os.path.getsize(file_path))
                }
                
                async with self._session.post(
                    url, 
                    params=params, 
                    data=f, 
                    headers=headers
                ) as response:
                    if response.status != 200:
                        error = await response.text()
                        raise MatrixError(f"Failed to upload file: {error}")
                        
                    result = await response.json()
                    return result.get('content_uri')
                    
        except Exception as e:
            raise MatrixError(f"Error uploading file: {e}") from e
    
    async def send_file(
        self, 
        room_id: str, 
        file_path: str, 
        message: Optional[str] = None,
        content_type: Optional[str] = None,
        filename: Optional[str] = None
    ) -> str:
        """
        Send a file to a room.
        
        Args:
            room_id: ID of the room to send the file to
            file_path: Path to the file to send
            message: Optional message to include with the file
            content_type: Content type of the file (optional)
            filename: Filename to use (optional)
            
        Returns:
            str: Event ID of the sent message
        """
        mxc_uri = await self.upload_file(file_path, content_type, filename)
        
        msg = Message(room=self.rooms.get(room_id))
        await msg.add_attachment(
            url=mxc_uri,
            filename=filename or os.path.basename(file_path),
            mimetype=content_type
        )
        
    
    async def send_message(self, room_id: str, content=None, file=None, **kwargs):
        """Send a message to a room.
        
        Args:
            room_id: ID of the room to send the message to
            content: The message content (string or dict). Can't be used with 'file'.
            file: Path to a file to upload and send. Can't be used with 'content'.
            **kwargs: Additional message parameters
            
        Returns:
            str: The event ID of the sent message
            
        Raises:
            ValueError: If both 'content' and 'file' are specified
        """
        if content and file:
            raise ValueError("Simultaneous sending of text and file is not allowed. Use two separate calls.")
        if file:
            if not os.path.exists(file):
                raise FileNotFoundError(f"File not found: {file}")
                
            mime_type = mimetypes.guess_type(file)[0] or 'application/octet-stream'
            
            with open(file, 'rb') as f:
                mxc_url = await self.upload_file(
                    file_path=file,
                    content_type=mime_type,
                    filename=os.path.basename(file)
                )
            
            if mime_type.startswith('image/'):
                try:
                    from PIL import Image
                    with Image.open(file) as img:
                        width, height = img.size
                except ImportError:
                    width, height = None, None
                
                msg_content = {
                    "body": os.path.basename(file),
                    "info": {
                        "size": os.path.getsize(file),
                        "mimetype": mime_type,
                    },
                    "msgtype": "m.image",
                    "url": mxc_url
                }
                
                if width and height:
                    msg_content["info"]["w"] = width
                    msg_content["info"]["h"] = height
                
                if isinstance(content, str):
                    msg_content["body"] = f"{content}\n{msg_content['body']}"
                
                content = msg_content
            else:
                content = {
                    "body": content or os.path.basename(file),
                    "filename": os.path.basename(file),
                    "msgtype": "m.file",
                    "url": mxc_url,
                    "info": {
                        "size": os.path.getsize(file),
                        "mimetype": mime_type
                    }
                }
        
        if isinstance(content, str):
            message = {
                "msgtype": "m.text",
                "body": content,
                **kwargs
            }
        else:
            message = content
            message.update(kwargs)
            
        try:
            response = await self._api_put(
                f"/_matrix/client/r0/rooms/{room_id}/send/m.room.message/{int(time.time() * 1000)}",
                message
            )
            return response.get('event_id')
        except MatrixAPIError as e:
            raise MatrixError(f"Failed to send message: {e}") from e
    
    async def get_displayname(self, user_id: str) -> Optional[str]:
        """Get a user's display name."""
        try:
            profile = await self._api_get(f"/_matrix/client/r0/profile/{user_id}/displayname")
            return profile.get('displayname')
        except MatrixAPIError:
            return None

    async def get_avatar_url(self, user_id: str) -> Optional[str]:
        """Get a user's avatar URL."""
        try:
            profile = await self._api_get(f"/_matrix/client/r0/profile/{user_id}/avatar_url")
            return profile.get('avatar_url')
        except MatrixAPIError:
            return None

    async def get_presence(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get a user's presence status."""
        try:
            presence = await self._api_get(f"/_matrix/client/r0/presence/{user_id}/status")
            return presence
        except MatrixAPIError:
            return None

    async def send_reaction(self, room_id: str, event_id: str, emoji: str) -> str:
        """Add a reaction to a message."""
        content = {
            'm.relates_to': {
                'rel_type': 'm.annotation',
                'event_id': event_id,
                'key': emoji
            }
        }
        
        try:
            response = await self._api_put(
                f"/_matrix/client/r0/rooms/{room_id}/send/m.reaction/{int(time.time() * 1000)}",
                content
            )
            return response.get('event_id')
        except MatrixAPIError as e:
            raise MatrixError(f"Failed to add reaction: {e}") from e
    
    # ===== Synchronisation =====
    
    async def sync(self, timeout: int = 30000, retry_count: int = 0) -> None:
        """Start synchronization with the server."""
        self._should_sync = True
        max_retries = 5
        
        while self._should_sync:
            try:
                backoff = min(2 ** retry_count, 30)
                if retry_count > 0:
                    logger.warning(f"Retry {retry_count}/{max_retries} - Waiting {backoff} seconds before next attempt...")
                    await asyncio.sleep(backoff)
                
                params = {
                    'timeout': str(timeout),
                    'full_state': 'false',
                    'set_presence': 'online',
                }
                
                if self._sync_token:
                    params['since'] = self._sync_token
                    logger.debug(f"Using sync token: {self._sync_token}")
                else:
                    logger.debug("No sync token available, initial sync")
                
                if not self._first_sync_complete:
                    params['filter'] = json.dumps({
                        'room': {
                            'state': {},
                            'timeline': {'limit': 1},
                            'ephemeral': {'limit': 0},
                        }
                    })
                
                if self._sync_token:
                    params['since'] = self._sync_token
                    logger.debug(f"Synchronizing with token: {self._sync_token}")
                else:
                    logger.debug("First synchronization (no token)")
                
                logger.debug(f"Sending sync request with parameters: {params}")
                
                try:
                    response = await self._api_get('/_matrix/client/r0/sync', params=params)
                    
                    if response:
                        logger.debug(f"Sync response received with keys: {list(response.keys())}")
                        
                        if 'next_batch' in response:
                            logger.debug(f"Received next_batch token: {response['next_batch']}")
                        
                        if 'rooms' in response and response['rooms']:
                            rooms = response['rooms']
                            logger.debug(f"Rooms in response - joined: {len(rooms.get('join', {}))}, "
                                       f"invited: {len(rooms.get('invite', {}))}, "
                                       f"left: {len(rooms.get('leave', {}))}")
                    else:
                        logger.warning("Empty sync response received")
                    
                except Exception as e:
                    logger.error(f"Error during sync request: {e}")
                    if retry_count < max_retries:
                        return await self.sync(timeout, retry_count + 1)
                    raise
                
                new_sync_token = response.get('next_batch')
                
                if new_sync_token:
                    logger.debug(f"Updating sync token: {new_sync_token}")
                    self._sync_token = new_sync_token
                    
                    retry_count = 0
                else:
                    logger.warning("No next_batch token in sync response")
                    if retry_count < max_retries:
                        return await self.sync(timeout, retry_count + 1)
                    
                    logger.warning("Max retries reached, waiting before next attempt...")
                    await asyncio.sleep(5)
                    continue
                
                if response:
                    logger.debug(f"Response type: {type(response)}")
                    logger.debug(f"Response keys: {list(response.keys())}")
                    
                    if 'rooms' in response and response['rooms']:
                        joined_rooms = response['rooms'].get('join', {})
                        if joined_rooms:
                            logger.debug(f"Joined rooms in response: {list(joined_rooms.keys())}")
                        invited_rooms = response['rooms'].get('invite', {})
                        if invited_rooms:
                            logger.debug(f"Invited rooms in response: {list(invited_rooms.keys())}")
                        left_rooms = response['rooms'].get('leave', {})
                        if left_rooms:
                            logger.debug(f"Left rooms in response: {list(left_rooms.keys())}")
                else:
                    logger.warning("Empty response received from sync endpoint")
                
                token_updated = False
                if new_sync_token:
                    old_token = self._sync_token
                    self._sync_token = new_sync_token
                    token_updated = True
                    
                    if not self._first_sync_complete:
                        self._first_sync_complete = True
                        logger.info(f"First sync completed successfully with token: {self._sync_token}")
                        
                        for handler in self._ready_handlers:
                            try:
                                if asyncio.iscoroutinefunction(handler):
                                    await handler()
                                else:
                                    handler()
                            except Exception as e:
                                logger.error(f"Error in on_ready handler: {e}", exc_info=True)
                    else:
                        logger.debug(f"Sync token updated: {self._sync_token}")
                
                if response:
                    await self._handle_sync_response(response)
                elif not self._first_sync_complete:
                    logger.warning("No response received during first sync, will retry...")
                    await asyncio.sleep(1)
                    continue
                
            except asyncio.CancelledError:
                logger.debug("Synchronization cancelled")
                break
            except Exception as e:
                if not self._first_sync_complete:
                    logger.debug(f"Error during first sync: {e}")
                    self._first_sync_complete = True
                    await asyncio.sleep(1)
                else:
                    logger.error(f"Error during synchronization: {e}", exc_info=True)
                    await asyncio.sleep(5)
    
    async def _run_sync_loop(self):
        """Synchronization loop with error handling."""
        logger.debug("Starting sync loop")
        sync_attempts = 0
        while self._should_sync:
            sync_attempts += 1
            logger.debug(f"Sync attempt #{sync_attempts}")
            try:
                await self.sync()
            except asyncio.CancelledError:
                logger.debug("Synchronization loop cancelled")
                break
            except Exception as e:
                logger.error(f"Error in synchronization loop: {e}", exc_info=True)
                await asyncio.sleep(5)
            
            if self._should_sync:
                await asyncio.sleep(1)
    
    async def start_sync(self) -> None:
        """Start background synchronization."""
        if self._sync_task is not None and not self._sync_task.done():
            logger.debug("Synchronization task is already running")
            return
        
        logger.info("Starting synchronization loop...")
        self._should_sync = True
        
        if self._session is None or self._session.closed:
            logger.debug("Initializing HTTP session for sync")
            self._session = aiohttp.ClientSession()
        
        self._sync_task = asyncio.create_task(self._run_sync_loop())
        
        def handle_task_result(task):
            try:
                task.result()
            except asyncio.CancelledError:
                logger.debug("Synchronization task was cancelled")
            except Exception as e:
                logger.error(f"Synchronization task failed: {e}", exc_info=True)
                if self._should_sync:
                    logger.info("Restarting synchronization in 5 seconds...")
                    asyncio.get_event_loop().call_later(5, lambda: asyncio.create_task(self.start_sync()))
        
        self._sync_task.add_done_callback(handle_task_result)
        
        logger.info("Synchronization loop started")
    
    async def stop_sync(self) -> None:
        """Stop background synchronization."""
        self._should_sync = False
        if self._sync_task and not self._sync_task.done():
            self._sync_task.cancel()
            try:
                await self._sync_task
            except asyncio.CancelledError:
                pass
        self._sync_task = None
    
    # ===== Event Handling =====
    
    def on_ready(self, func=None):
        """
        Decorator to define a function to call when the bot is ready.
        
        Usage:
            @client.on_ready()
            async def on_ready():
                print("Bot ready!")
        """
        def decorator(f):
            self._ready_handlers.append(f)
            return f
            
        if func is None:
            return decorator
        return decorator(func)
        
    def event(self, event_type: str = None):
        """Decorator to register an event handler.
        
        Can be used as:
            @client.event
            async def on_message(event):
                pass
                
        or:
            @client.event('custom_event')
            async def handler(event):
                pass
        """
        if callable(event_type):
            func = event_type
            if func.__name__.startswith('on_'):
                event_name = func.__name__
            else:
                event_name = f"on_{func.__name__}"
                
            if event_name not in self.event_handlers:
                self.event_handlers[event_name] = []
            self.event_handlers[event_name].append(func)
            logger.debug(f"Registered event handler for {event_name}: {func.__name__}")
            return func
            
        def decorator(func):
            nonlocal event_type
            if event_type is None:
                if func.__name__.startswith('on_'):
                    event_type = func.__name__
                else:
                    event_type = f"on_{func.__name__}"
                    
            if event_type not in self.event_handlers:
                self.event_handlers[event_type] = []
            self.event_handlers[event_type].append(func)
            logger.debug(f"Registered event handler for {event_type}: {func.__name__}")
            return func
            
        return decorator
        
    def command(self, name: str = None, aliases: list = None, **attrs):
        """Decorator to register a command.
        
        Args:
            name: Name of the command (if different from the function name)
            aliases: List of aliases for the command
            **attrs: Additional attributes for the command
            
        Example:
            @client.command()
            async def ping(ctx):
                await ctx.reply('Pong!')
        """
        def decorator(func):
            nonlocal name
            if name is None:
                name = func.__name__
            
            command = {
                'name': name,
                'func': func,
                'aliases': aliases or [],
                **attrs
            }
            
            self._commands[name] = command
            if aliases:
                for alias in aliases:
                    self._commands[alias] = command
                    
            return func
            
        return decorator
        
    async def _process_commands(self, event):
        """Process incoming messages to check for commands."""
        if event.sender == self.user_id:
            return
            
        if not hasattr(event, 'content') or 'body' not in event.content:
            return
            
        content = event.content['body']
        if not content.startswith(self.command_prefix):
            return
            
        args = content[len(self.command_prefix):].split()
        if not args:
            return
            
        command_name = args[0].lower()
        command = self._commands.get(command_name)
        
        if not command:
            for cmd in self._commands.values():
                if command_name in cmd.get('aliases', []):
                    command = cmd
                    break
                    
        if command:
            ctx = CommandContext(self, event, command_name, args[1:], command)
            
            try:
                await command['func'](ctx)
                for handler in self._event_handlers.get('on_command', []):
                    await handler(event, command_name, args[1:])
            except Exception as e:
                logger.error(f"Error executing command {command_name}: {e}", exc_info=True)
                try:
                    await ctx.reply(f"❌ Une erreur est survenue: {str(e)}")
                except:
                    pass
    
    async def _dispatch_event(self, event_type: str, *args, **kwargs):
        """Triggers event handlers for a given event type."""
        logger.debug(f"Dispatching event: {event_type}")
        logger.debug(f"Event handlers: {self.event_handlers}")
        if event_type in self.event_handlers:
            logger.debug(f"Found {len(self.event_handlers[event_type])} handlers for {event_type}")
            for i, handler in enumerate(self.event_handlers[event_type]):
                try:
                    logger.debug(f"Calling handler {i} for {event_type}: {handler}")
                    if asyncio.iscoroutinefunction(handler):
                        await handler(*args, **kwargs)
                    else:
                        handler(*args, **kwargs)
                except Exception as e:
                    logger.error(f"Error in {event_type} event handler: {e}", exc_info=True)
        else:
            logger.warning(f"No handlers registered for event type: {event_type}")
    
    # ===== Internal Methods =====
    
    async def _load_joined_rooms(self) -> bool:
        """
        Load all joined rooms via the API.
        
        Returns:
            bool: True if the loading was successful, False otherwise
        """
        try:
            if not hasattr(self, '_session') or self._session is None:
                logger.error("Session not initialized")
                return False
                
            if not hasattr(self, 'access_token') or not self.access_token:
                logger.error("Access token not available")
                return False
                
            logger.debug("Loading joined rooms via API...")
            
            response = await self._api_get("/_matrix/client/r0/joined_rooms")
            
            if not response or 'joined_rooms' not in response:
                logger.error(f"Unexpected API response: {response}")
                return False
                
            room_ids = response['joined_rooms']
            logger.debug(f"{len(room_ids)} rooms loaded")
            
            rooms_loaded = 0
            for room_id in room_ids:
                try:
                    if room_id not in self.rooms:
                        self.rooms[room_id] = Room(room_id, self)
                        logger.debug(f"New room loaded: {room_id}")
                        rooms_loaded += 1
                except Exception as room_error:
                    logger.error(f"Error loading room {room_id}: {room_error}")
            
            logger.info(f"Loading completed - {rooms_loaded} new rooms loaded (total: {len(self.rooms)})")
            return True
            
        except Exception as e:
            logger.error(f"Error loading rooms: {e}", exc_info=True)
            return False

    async def _handle_sync_response(self, response: Dict[str, Any]) -> None:
        """Processes the synchronization response and triggers the appropriate events."""
        if 'next_batch' in response and response['next_batch']:
            self._sync_token = response['next_batch']
            logger.debug(f"Updated synchronization token: {self._sync_token}")
        else:
            logger.warning("No synchronization token received in the response")
        
        rooms = response.get('rooms', {})
        logger.debug(f"Received sync response with {len(rooms.get('join', {}))} joined rooms, "
                    f"{len(rooms.get('invite', {}))} invites, {len(rooms.get('leave', {}))} left rooms")
        
        if not hasattr(self, '_first_sync_complete') or not self._first_sync_complete:
            logger.debug("First synchronization - Starting...")
            await self._load_joined_rooms()
            self._first_sync_complete = True
            logger.info(f"First synchronization complete - {len(self.rooms)} rooms loaded")
            
            for handler in self._ready_handlers:
                try:
                    if asyncio.iscoroutinefunction(handler):
                        await handler()
                    else:
                        handler()
                except Exception as e:
                    logger.error(f"Error in on_ready handler: {e}", exc_info=True)
        
        for room_id, room_data in rooms.get('join', {}).items():
            if room_id not in self.rooms:
                self.rooms[room_id] = Room(room_id, self)
                logger.info(f"New room detected: {room_id}")
            await self._handle_room_events(room_id, room_data)
        
        for room_id, room_data in rooms.get('invite', {}).items():
            if room_id not in self.rooms:
                self.rooms[room_id] = Room(room_id, self)
                logger.info(f"New invitation to room: {room_id}")
            await self._dispatch_event('on_room_invite', self.rooms[room_id])
        
        for room_id, room_data in rooms.get('leave', {}).items():
            if room_id in self.rooms:
                await self._dispatch_event('on_room_leave', self.rooms[room_id])
                del self.rooms[room_id]
                logger.info(f"Left room: {room_id}")

    async def _handle_room_events(self, room_id: str, room_data: Dict[str, Any]) -> None:
        """Processes events for a specific room."""
        try:
            if not room_id:
                logger.warning("Received empty room_id in _handle_room_events")
                return
                
            if room_id not in self.rooms:
                logger.debug(f"Creating new room: {room_id}")
                self.rooms[room_id] = Room(room_id, self)
            
            room = self.rooms[room_id]
            
            if not hasattr(room, 'id') or not room.id:
                room.id = room_id
                logger.debug(f"Set room ID to: {room_id}")
            
            if not hasattr(self, '_start_time') or self._start_time is None:
                self._start_time = int(time.time() * 1000)
                logger.debug(f"_start_time set to: {self._start_time}")
            
            timeline_events = room_data.get('timeline', {}).get('events', [])
            logger.debug(f"Processing {len(timeline_events)} events in room {room_id}")
            
            if not timeline_events:
                logger.debug(f"No events to process for room {room_id}")
                return
                
        except Exception as e:
            logger.error(f"Error initializing room {room_id}: {e}", exc_info=True)
            return
        
        for event_data in timeline_events:
            event_id = event_data.get('event_id', 'unknown')
            event_type = event_data.get('type', 'unknown')
            event_ts = event_data.get('origin_server_ts', 0)
            
            if not event_ts:
                logger.debug(f"Event {event_id} ignored: no timestamp")
                continue
            
            if event_type == 'm.room.redaction':
                redacts_event_id = event_data.get('redacts')
                if redacts_event_id:
                    logger.debug(f"Handling redaction event for ID: {redacts_event_id}")
                    
                    original_event = None
                    if hasattr(room, 'get_event'):
                        try:
                            original_event = room.get_event(redacts_event_id)
                        except Exception as e:
                            logger.warning(f"Unable to retrieve original event {redacts_event_id}: {e}")
                    
                    event_content = {}
                    relates_to = {}
                    
                    if original_event:
                        event_content = getattr(original_event, 'content', {})
                        relates_to = getattr(original_event, 'relates_to', {})
                    
                    event_data = {
                        'type': 'm.reaction',
                        'event_id': event_data.get('event_id'),
                        'sender': event_data.get('sender'),
                        'origin_server_ts': event_data.get('origin_server_ts', int(time.time() * 1000)),
                        'content': event_content,
                        'm.relates_to': relates_to,
                        'unsigned': {
                            'redacted_because': event_data,
                            'original_event': original_event.raw_data if original_event else None,
                            'redaction_event': True
                        },
                        'redacts': redacts_event_id
                    }
                    
                    if 'key' in event_content and 'key' not in relates_to:
                        event_data['m.relates_to']['key'] = event_content['key']
                    
                    event_type = 'm.reaction'
                    
                    logger.debug(f"Creating a reaction redaction event for ID: {redacts_event_id}")
                    logger.debug(f"Redaction event details: {json.dumps({
                        'type': 'm.reaction',
                        'event_id': event_data.get('event_id'),
                        'sender': event_data.get('sender'),
                        'redacts': event_data.get('redacts'),
                        'has_original_event': original_event is not None,
                        'content_keys': list(event_data.get('content', {}).keys()),
                        'relates_to': event_data.get('m.relates_to', {})
                    }, indent=2, default=str)}")
            
            if event_type not in ['m.room.message', 'm.reaction']:
                logger.debug(f"Event not handled of type {event_type}: {event_id}")
                continue
            
            current_time = int(time.time() * 1000)
            
            if event_ts < self._start_time - 5000:
                logger.debug(f"Message {event_id} ignored: too old (startup: {self._start_time}, message: {event_ts})")
                continue
            
            if event_ts > (current_time + 5000):
                logger.debug(f"Message {event_id} ignored: in the future (current: {current_time}, message: {event_ts})")
                continue
            
            logger.debug(f"Handling message {event_id} (ts: {event_ts}): {event_data.get('content', {}).get('body', '')}")
            
            await self._handle_event(room, event_data)
    
    async def _handle_event(self, room: Room, event_data: Dict[str, Any]) -> None:
        """Process an individual event."""
        event_id = event_data.get('event_id', 'unknown')
        event_type = event_data.get('type')
        
        try:
            if not hasattr(room, 'id') or not room.id:
                room_id = event_data.get('room_id')
                if room_id:
                    room.id = room_id
                    logger.debug(f"Room ID set to: {room_id}")
                else:
                    logger.warning(f"Missing room_id in event: {event_id}")
            
            event = event_factory(event_data, room)
            
            if hasattr(room, 'add_event'):
                room.add_event(event)
            
            if isinstance(event, MessageEvent):
                room_id = getattr(room, 'id', 'unknown')
                logger.debug(f"Dispatching message event: {event_id} (type: {event.type}, room: {room_id})")
                logger.debug(f"Message content: {getattr(event, 'content', 'No content')}")
                
                if not hasattr(event, 'sender') or not hasattr(event, 'content'):
                    logger.warning(f"Message event {event_id} is missing sender or content")
                    return
                
                if hasattr(event, 'content') and 'body' in event.content:
                    content = event.content['body']
                    if content.startswith(self.command_prefix):
                        await self._process_commands(event)
                
                await self._dispatch_event('on_message', event)
                
            elif isinstance(event, ReactionEvent):
                logger.debug(f"Processing reaction event: {event_id}")
                logger.debug(f"Reaction details - relates_to: {getattr(event, 'relates_to', {})}")
                logger.debug(f"Reaction content: {getattr(event, 'content', {})}")
                
                if event.is_removal:
                    logger.debug(f"Dispatching reaction deleted event: {event_id}")
                    await self._dispatch_event('on_reaction_deleted', event)
                else:
                    logger.debug(f"Dispatching reaction added event: {event_id}")
                    await self._dispatch_event('on_reaction', event)
            else:
                logger.debug(f"Event not handled of type {event_type}: {event_id}")
                logger.debug(f"Event content: {getattr(event, 'content', 'No content')}")
                
        except Exception as e:
            logger.error(f"Error processing event {event_id} (type: {event_type}): {e}", exc_info=True)
            logger.debug(f"Event data: {json.dumps(event_data, indent=2, default=str)}")
    
    async def _resolve_room_alias(self, alias: str) -> str:
        """Resolves a room alias to a room ID."""
        try:
            alias = alias.lstrip('#')
            response = await self._api_get(f"/_matrix/client/r0/directory/room/{alias}")
            return response.get('room_id')
        except MatrixAPIError as e:
            raise MatrixRoomError(f"Could not resolve room alias {alias}") from e
    
    # ===== HTTP API Methods =====
    
    async def _api_request(self, method: str, endpoint: str, **kwargs) -> Union[Dict[str, Any], bytes]:
        """Makes an HTTP request to the Matrix API.
        
        Args:
            method: HTTP method (GET, POST, PUT, etc.)
            endpoint: API endpoint (e.g., '/sync' or full URL)
            **kwargs: Additional arguments passed to aiohttp.request
            
        Keyword Args:
            auth_required: If True (default), adds the access token to headers
            binary: If True, returns raw bytes instead of parsing JSON
            
        Returns:
            Union[Dict[str, Any], bytes]: Parsed JSON response or raw bytes if binary=True
        """
        auth_required = kwargs.pop('auth_required', True)
        binary = kwargs.pop('binary', False)
        
        if endpoint.startswith('http'):
            url = endpoint
        else:
            if (endpoint.startswith('_matrix/media/') and 
                self.homeserver == 'https://matrix.org' and 
                not endpoint.startswith('_matrix/media/r0/download/')):
                base_url = 'https://matrix-client.matrix.org'
                endpoint = endpoint.lstrip('/')
                url = f"{base_url}/{endpoint}"
            else:
                if endpoint.startswith('/_matrix/client/r0'):
                    endpoint = endpoint[len('/_matrix/client/r0'):]
                elif not endpoint.startswith('/'):
                    endpoint = '/' + endpoint
                
                base_url = self.homeserver.rstrip('/')
                url = f"{base_url}/_matrix/client/r0{endpoint}"
        
        headers = kwargs.pop('headers', {})
        if auth_required and self.access_token and 'Authorization' not in headers:
            headers['Authorization'] = f'Bearer {self.access_token}'
        
        if 'User-Agent' not in headers:
            headers['User-Agent'] = 'matryx/1.0'
        
        request_id = str(id(self))
        logger.debug(f"[{request_id}] Sending {method} request to {url}")
        logger.debug(f"[{request_id}] Headers: {headers}")
        if 'params' in kwargs:
            logger.debug(f"[{request_id}] Parameters: {kwargs['params']}")
        
        try:
            async with self._session.request(
                method=method,
                url=url,
                headers=headers,
                **kwargs
            ) as response:
                if binary:
                    if response.status >= 400:
                        error_text = await response.text()
                        logger.error(f"[{request_id}] Error {response.status}: {error_text}")
                        raise MatrixAPIError(
                            f"Error {response.status}: {error_text}",
                            status_code=response.status
                        )
                    return await response.read()
                
                try:
                    data = await response.json()
                    logger.debug(f"[{request_id}] Response received: {response.status} {response.reason}")
                    logger.debug(f"[{request_id}] Data: {json.dumps(data, indent=2)[:500]}...")  # Limit log size
                except Exception as e:
                    text = await response.text()
                    logger.error(f"[{request_id}] JSON read error: {e}")
                    logger.error(f"[{request_id}] Raw response: {text[:1000]}")
                    raise
                
                if response.status >= 400:
                    error_msg = data.get('error', 'Unknown error')
                    errcode = data.get('errcode', 'M_UNKNOWN')
                    
                    if response.status == 403:
                        raise ForbiddenError(
                            f"Access denied: {error_msg}",
                            status_code=response.status,
                            error_code=errcode
                        )
                    elif response.status == 404:
                        logger.error(f"[{request_id}] Erreur {response.status} ({errcode}): {error_msg}")
                        raise NotFoundError(
                            f"Resource not found: {error_msg}",
                            status_code=response.status,
                            error_code=errcode
                        )
                    
                    logger.error(f"[{request_id}] Error {response.status} ({errcode}): {error_msg}")
                    raise MatrixAPIError(
                        f"Error {response.status} ({errcode}): {error_msg}",
                        status_code=response.status,
                        error_code=errcode
                    )
                
                return data
                
        except aiohttp.ClientError as e:
            raise MatrixConnectionError(f"Connection error: {e}") from e
        except Exception as e:
            if not (isinstance(e, ForbiddenError) or isinstance(e, NotFoundError)):
                logger.error(f"[{request_id}] Unexpected error: {str(e)}", exc_info=True)
            raise
    
    async def _api_get(self, endpoint: str, **kwargs) -> Dict[str, Any]:
        """Performs a GET request to the Matrix API."""
        logger.debug(f"Making GET request to {endpoint}")
        logger.debug(f"Request parameters: {kwargs.get('params', {})}")
        
        try:
            response = await self._api_request('GET', endpoint, **kwargs)
            logger.debug(f"GET request to {endpoint} successful")
            return response
        except Exception as e:
            logger.error(f"Error in GET request to {endpoint}: {str(e)}")
            if hasattr(e, 'response') and hasattr(e.response, 'status'):
                logger.error(f"Response status: {e.response.status}")
                try:
                    error_body = await e.response.text()
                    logger.error(f"Response body: {error_body}")
                except:
                    pass
            raise
    
    async def _api_post(self, endpoint: str, data: Dict = None, **kwargs) -> Dict[str, Any]:
        """Performs a POST request to the Matrix API."""
        kwargs['json'] = data or {}
        return await self._api_request('POST', endpoint, **kwargs)
    
    async def _api_put(self, endpoint: str, data: Dict = None, **kwargs) -> Dict[str, Any]:
        """Performs a PUT request to the Matrix API."""
        kwargs['json'] = data or {}
        return await self._api_request('PUT', endpoint, **kwargs)
