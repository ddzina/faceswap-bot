"""
This module, db_requests.py, provides an asynchronous interface to interact with a SQLite database using
SQLAlchemy and aiosqlite. It defines models for Users, Messages, and ImageNames, leveraging SQLAlchemy's ORM
capabilities for database operations. The module supports operations such as initializing the database, inserting
and updating user information, handling message and image name records, and adjusting user quotas.

Key Features:
- Asynchronous database engine creation and session management using SQLAlchemy's async capabilities.
- ORM model definitions for User, Message, and ImageName, facilitating easy data manipulation and query construction.
- Utility functions for database initialization, user data manipulation (insertion, updates), message logging,
  image name handling, and user data fetching.
- Advanced user management features including premium status upgrades, request and target quota adjustments,
  and timestamp updates for user activities.

Usage:
The module is designed to be used in asynchronous Python applications where database interactions are required.
It includes functions to perform CRUD operations on user data, manage message and image name records, and retrieve
user-specific information efficiently. The async functions ensure non-blocking database operations, suitable for
I/O-bound applications like chatbots or web services.

Examples of operations include inserting a new user, updating a user's mode, decrementing request quotas, logging
messages, and fetching user data. These operations are encapsulated in async functions, which need to be awaited
when called.

Note: Before using this module, ensure the database file path and SQLAlchemy engine settings are correctly configured
for your application's requirements. Also make sure all necessary config and yaml files are set on the root level.
TODO: split file into several ones
"""

from datetime import datetime, timedelta, date
from sqlalchemy import Column, Integer, String, TIMESTAMP, Date, ForeignKey, func, select, desc, delete
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import selectinload
from typing import Dict, Optional, List, Any, Union
from bot.handlers.constants import HOUR_INTERVAL, PREMIUM_DAYS, DATEFORMAT, DATABASE_FILE, ASYNC_DB_URL

# Set  credentials and run DB
Base = declarative_base()  # Class name after all
async_engine = create_async_engine(ASYNC_DB_URL, echo=True)


class PremiumPurchase(Base):
    __tablename__: str = 'premium_purchases'
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    purchase_date = Column(Date, default=date.today)
    expiration_date = Column(Date)
    targets_increment = Column(Integer, default=10)
    request_increment = Column(Integer, default=100)

    user = relationship("User", back_populates='premium_purchases')


class User(Base):
    __tablename__: str = 'users'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, unique=True)
    username = Column(String)
    first_name = Column(String)
    last_name = Column(String)
    mode = Column(Integer, default=1)
    receive_target_flag = Column(Integer, default=0)
    status = Column(String, default='free')
    requests_left = Column(Integer, default=10)
    targets_left = Column(Integer, default=0)
    last_photo_sent_timestamp = Column(TIMESTAMP, default=datetime.now())
    premium_expiration = Column(Date, nullable=True)

    messages = relationship("Message", back_populates="user")
    image_names = relationship("ImageName", back_populates="user")
    premium_purchases = relationship("PremiumPurchase", back_populates="user", order_by='PremiumPurchase.purchase_date')


class Message(Base):
    __tablename__: str = 'messages'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('users.id'))
    text_data = Column(String)
    timestamp = Column(TIMESTAMP, server_default=func.now())

    user = relationship("User", back_populates="messages")


class ImageName(Base):
    __tablename__: str = 'image_names'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('users.id'))
    input_image_name = Column(String)
    output_image_names = Column(String)
    timestamp = Column(TIMESTAMP, default=datetime.now())

    user = relationship("User", back_populates="image_names")


class SchedulerLog(Base):
    __tablename__ = 'scheduler_logs'

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_name = Column(String, nullable=False)
    run_datetime = Column(TIMESTAMP, server_default=func.now())
    status = Column(String, nullable=False)
    details = Column(String, nullable=True)

    def __repr__(self):
        return f"<SchedulerLog(job_name='{self.job_name}', run_datetime='{self.run_datetime}', " \
               f"status='{self.status}', details='{self.details}')>"


class ErrorLog(Base):
    __tablename__ = 'error_logs'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('users.user_id'), nullable=True)
    error_message = Column(String, nullable=False)
    timestamp = Column(TIMESTAMP, server_default=func.now())
    details = Column(String, nullable=True)


async def log_error(user_id: Optional[int], error_message: str, details: Optional[str] = None) -> None:
    async with AsyncSession(async_engine) as session:
        async with session.begin():
            error_log = ErrorLog(
                user_id=user_id,
                error_message=error_message,
                details=details
            )
            session.add(error_log)
            await session.commit()


async def fetch_recent_errors(limit: int = 10) -> List[Dict[str, Any]]:
    """
    Fetch the most recent error logs.

    :param limit: The number of recent error logs to fetch.
    :return: A list of dictionaries, each representing an error log.
    """
    async with AsyncSession(async_engine) as session:
        async with session.begin():
            result = await session.execute(
                select(
                    ErrorLog.id,
                    ErrorLog.user_id,
                    ErrorLog.error_message,
                    ErrorLog.details,
                    ErrorLog.timestamp,
                    User.username,
                    User.first_name,
                    User.last_name)
                .join(User, ErrorLog.user_id == User.user_id, isouter=True)
                .order_by(ErrorLog.timestamp.desc())
                .limit(limit)
            )
            error_logs = result.all()
            recent_errors = [{
                "id": error_log.id,
                "user_id": error_log.user_id,
                "error_message": error_log.error_message,
                "details": error_log.details,
                "timestamp": error_log.timestamp,
                "username": error_log.username,
                "first_name": error_log.first_name,
                "last_name": error_log.last_name
            } for error_log in error_logs]
            return recent_errors


async def log_scheduler_run(job_name: str, status: str = "success", details: str = None,
                            hour_delay: int = HOUR_INTERVAL):
    """
    Logs a scheduler run if hour interval hours have passed since the last run, or creates an entry if none exists.

    :param job_name: The name of the job that was run.
    :param status: The status of the job run (default is 'success').
    :param details: Optional details about the job run.
    :param hour_delay: The delay in hours to check against the last run time (default is hour interval hours).
    :return: None
    """
    async with AsyncSession(async_engine) as session:
        async with session.begin():
            # Check the last entry for the job
            last_entry = await session.execute(
                select(SchedulerLog)
                .filter_by(job_name=job_name)
                .order_by(desc(SchedulerLog.run_datetime))
                .limit(1)
            )
            last_entry = last_entry.scalar_one_or_none()

            now = datetime.now()

            # If there's no last entry or hour interval hours have passed since the last run, log a new entry
            if not last_entry or now - last_entry.run_datetime >= timedelta(hours=hour_delay):
                new_log = SchedulerLog(job_name=job_name, status=status, details=details)
                session.add(new_log)
                await session.commit()
                print(f"Logged new run for {job_name}.")
            else:
                print(f"No need to log {job_name} yet.")
            await fetch_scheduler_logs(job_name)


async def clear_output_images_by_user_id(user_id: int, hour_delay: int = HOUR_INTERVAL) -> None:
    """
    Clears (deletes) output image names associated with a given user ID.

    :param user_id: The Telegram ID of the user whose output images are to be cleared.
    :param hour_delay: How often should the data be cleaned.
    :return: None
    """
    cutoff_time = datetime.now() - timedelta(hours=hour_delay)
    async with AsyncSession(async_engine) as session:
        async with session.begin():
            await session.execute(
                delete(ImageName)
                .where(ImageName.user_id == user_id, ImageName.timestamp < cutoff_time)
            )
            await session.commit()


async def clear_outdated_images(hour_delay: int = HOUR_INTERVAL):
    """
    Clears outdated output images for all users in the database.

    :param hour_delay: The age threshold in hours for an image to be considered outdated.
    :return: None
    """
    async with AsyncSession(async_engine) as session:
        async with session.begin():
            result = await session.execute(select(User.user_id))
            user_ids = result.scalars().all()
    for user_id in user_ids:
        await clear_output_images_by_user_id(user_id, hour_delay)
        print(f"Cleared outdated images for user ID: {user_id}")


async def initialize_database() -> None:
    """"
    Initialize and create the tables in the database asynchronously
    """
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def insert_user(user_id: int, username: str, first_name: str, last_name: str, mode: int = 1):
    """
    Inserts a new user or updates an existing user's information.

    :param user_id: The user's tg ID.
    :param username: The username of the user.
    :param first_name: The first name of the user.
    :param last_name: The last name of the user.
    :param mode: The user's mode (default is 1).
    :return: None
    """
    async with AsyncSession(async_engine) as session:
        async with session.begin():
            existing_user = await session.execute(select(User).filter_by(user_id=user_id))
            existing_user = existing_user.scalar_one_or_none()
            # Avoid sql injection
            username = username.replace(' ', '_')
            first_name = first_name.replace(' ', '_')
            last_name = last_name.replace(' ', '_')
            if existing_user is None:

                user = User(user_id=user_id, username=username, first_name=first_name, last_name=last_name, mode=mode)
                session.add(user)
            else:
                existing_user.username = username
                existing_user.first_name = first_name
                existing_user.last_name = last_name
            await session.commit()


async def update_user_mode(user_id: int, mode: str):
    """
    Updates the mode of an existing user.

    :param user_id: The user's tg ID.
    :param mode: The new user mode.
    :return: None
    """
    async with AsyncSession(async_engine) as session:
        async with session.begin():
            user = await session.execute(select(User).filter_by(user_id=user_id))
            user = user.scalar_one_or_none()
            if user:
                user.mode = mode
            await session.commit()


async def decrement_requests_left(user_id: int, n: int = 1) -> None:
    """
    Decrements the max number of images user can receive.

    :param user_id: The user's tg ID.
    :param n: The number to decrement (default is 1).
    :return: None
    """
    async with AsyncSession(async_engine) as session:
        async with session.begin():
            user = await session.execute(select(User).filter_by(user_id=user_id))
            user = user.scalar_one_or_none()
            if user:
                if user.requests_left > 0:
                    n = user.requests_left - n
                    user.requests_left = max(0, n)
                else:
                    user.requests_left = 0
            await session.commit()


async def decrement_targets_left(user_id: int, n: int = 1) -> None:
    """
    Decrements the number of targets user can upload.

    :param user_id: The user's tg ID.
    :param n: The number to decrement (default is 1).
    :return: None
    """
    async with AsyncSession(async_engine) as session:
        async with session.begin():
            user = await session.execute(select(User).filter_by(user_id=user_id))
            user = user.scalar_one_or_none()
            if user and user.targets_left > 0:
                n = user.targets_left - n
                user.targets_left = max(0, n)
            await session.commit()


async def set_requests_left(user_id: int, number: int = 10) -> None:
    """
    Set the number of images user can receive.

    :param user_id: The user's tg ID.
    :param number: The number of requests_left (default is 10).
    :return: None
    """
    async with AsyncSession(async_engine) as session:
        async with session.begin():
            user = await session.execute(select(User).filter_by(user_id=user_id))
            user = user.scalar_one_or_none()
            if user:
                user.status = 'free'
                user.premium_expiration = None
                user.premium_purchases = None
                user.targets_left = 0
                user.requests_left = number
            await session.commit()


async def buy_premium(user_id: int) -> None:
    """
    Upgrade a user to premium and update their quotas.

    :param user_id: The user's tg ID.
    :return: None
    """
    async with AsyncSession(async_engine) as session:
        async with session.begin():
            user = await session.execute(select(User).filter_by(user_id=user_id))
            user = user.scalar_one_or_none()

            if user:
                new_expiration_date = datetime.now() + timedelta(days=PREMIUM_DAYS)

                # Create a new PremiumPurchase record
                new_premium = PremiumPurchase(
                    user_id=user.user_id,
                    purchase_date=datetime.now().date(),
                    expiration_date=new_expiration_date.date(),
                    targets_increment=10,
                    request_increment=100
                )
                session.add(new_premium)

                user.requests_left += 100
                user.targets_left += 10
                user.status = "premium"
                user.premium_expiration = new_expiration_date  # 30 days for premium
            await session.commit()


async def insert_message(user_id: int, text_data: str) -> None:
    """
    Insert a message associated with a user into a DB.

    :param user_id: The user's tg ID.
    :param text_data: The text data of the message.
    :return: None
    """
    async with AsyncSession(async_engine) as session:
        async with session.begin():
            user = await session.execute(select(User).filter_by(user_id=user_id))
            user = user.scalar_one_or_none()

            if user:
                message = Message(user_id=user.id, text_data=text_data)
                session.add(message)
            await session.commit()


async def fetch_image_names_by_user_id(user_id: int) -> Dict[str, Any]:
    """
    Fetch image names associated with a user by their user ID.

    :param user_id: The user's tg ID.
    :return: A dictionary mapping DBs input image names to output image names (or None if no output image names exist).
    """
    async with AsyncSession(async_engine) as session:
        result = await session.execute(
            select(ImageName).filter_by(user_id=user_id)
        )
        image_names = result.scalars().all()

        image_name_dict = {}
        if image_names:
            for image_name in image_names:
                image_name_dict[image_name.input_image_name] = {
                    "output_image_names": image_name.output_image_names,
                    "timestamp": image_name.timestamp.strftime(DATEFORMAT)}
        return image_name_dict


async def create_image_entry(user_id: int, input_image_name: str) -> None:
    """
    Create a new image entry for a user.

    :param user_id: The user's tg ID.
    :param input_image_name: The input image name.
    :return: None
    """
    async with AsyncSession(async_engine) as session:
        async with session.begin():
            # Create a new entry in ImageName table
            new_entry = ImageName(user_id=user_id,
                                  input_image_name=input_image_name,
                                  output_image_names=None,
                                  timestamp=datetime.now())
            session.add(new_entry)
            await session.commit()


async def update_image_entry(user_id: int, input_image_name: str, output_image_names: List[str]) -> None:
    """
    Update an existing image entry for user.

    :param user_id: The user's tg ID to map inputs table to.
    :param input_image_name: The input image name to map outputs to.
    :param output_image_names: List of output image names.
    :return: None
    """
    async with AsyncSession(async_engine) as session:
        async with session.begin():
            existing_entry = await session.execute(select(ImageName).filter_by(
                user_id=user_id, input_image_name=input_image_name))
            existing_entry = existing_entry.scalar_one_or_none()
            str_outputs = ','.join(output_image_names) if output_image_names else None
            if existing_entry:
                existing_entry.output_image_names = str_outputs
                existing_entry.timestamp = datetime.now()
            else:
                print("Entry not found for update.")
            await session.commit()


async def exist_user_check(user: Any) -> None:
    """
    Check if a user exists and insert their information if not.
    Should be a wrapper but it throws coroutine TypeError

    :param user: The user aiogram object.
    :return: None
    """
    user_id = user.id
    username = user.username or ''
    first_name = user.first_name or ''
    last_name = user.last_name or ''
    await insert_user(user_id, username, first_name, last_name)


# exist_user_check - wrappers don't want to async work
async def log_text_data(message: Any) -> None:
    """
    Log text data from a message to a message and user table.

    :param message: The user's tg object.
    :return: None
    """
    await exist_user_check(message.from_user)
    text = message.text.replace(' ', '_')
    await insert_message(message.from_user.id, text)


# exist_user_check - wrappers don't want to async work
async def log_input_image_data(message: Any, input_image_name: str) -> None:
    """
    Log input image data to an image and user table.

    :param message: The user's tg message object.
    :param input_image_name: The name of the input image.
    :return: None
    """
    await exist_user_check(message.from_user)
    await create_image_entry(message.from_user.id, input_image_name)


async def log_output_image_data(message: Any, input_image_name: str,
                                output_image_names: Union[str, List[str], None]) -> None:
    """
    Log output image data.

    :param message: The user's tg message object.
    :param input_image_name: The name of the input image.
    :param output_image_names: The name(s) of the output image(s).
    :return: None
    """
    await exist_user_check(message.from_user)
    await update_image_entry(message.from_user.id, input_image_name, output_image_names)


async def run_sync_db_operation(operation: callable) -> None:
    """
        Run a synchronous database operation.

        :param operation: A callable function that performs a database operation.
        :return: None
        """
    async with AsyncSession(async_engine) as session:
        async with session.begin():
            operation(session)
            await session.commit()


async def fetch_user_data(user_id: int) -> Optional[Any]:
    """
    Fetch user data from the database tables.

    :param user_id: The tg ID of the user.
    :return: A User object or None if the user is not found.
    """
    async with AsyncSession(async_engine) as session:
        result = await session.execute(
            select(User).where(User.user_id == user_id).options(selectinload(User.messages)))
        user = result.scalar_one_or_none()

        if user:
            messages = ', '.join([message.text_data for message in user.messages])
            await format_userdata_output(user, messages)
            return user
        else:
            print("User not found")
            return None


async def format_userdata_output(user: User, messages: str) -> None:
    """
    Format user data output.

    :param user: The User object.
    :param messages: A string containing user messages.
    :return: None
    """
    print('_' * 50)
    image_names_dict = await fetch_image_names_by_user_id(user.user_id)
    if image_names_dict:
        image_names = '\n\t\t\t'.join([
                     f"original: {input_image} timestamp: {details['timestamp']}\n\t\t\t\t"
                     f"output [{len(details['output_image_names'].split(',')) if details['output_image_names'] else 0}"
                     f" img]: {details['output_image_names']})" for input_image, details in image_names_dict.items()])

        n = sum([len(details['output_image_names'].split(','))
                 if details['output_image_names'] else 0 for details in image_names_dict.values()])

        premium_purchases = await fetch_premium_purchases_by_user_id(user.user_id)
        premium_purchases_output = '\n\t\t\t'.join([
            f"Purchase Date: {purchase['purchase_date']}, Expiration Date: {purchase['expiration_date']}, "
            f"Targets Increment: {purchase['targets_increment']}, Requests Increment: {purchase['request_increment']}"
            for purchase in premium_purchases
        ])

        print(f"\n\n\nUser: {user.username} (ID:{user.user_id} Name: {user.first_name} {user.last_name})"
              f"\n\tMode: {user.mode} \n\tCustom targets left: {True if user.receive_target_flag else False} "
              f"- {user.targets_left} til {user.premium_expiration}"
              f"\n\tPremium Purchases: [{premium_purchases_output if premium_purchases_output else 'None'}]"
              f"\n\tStatus: {user.status}"
              f"\n\tRequests total: {len(image_names_dict.keys())} + {n}"
              f" left: {user.requests_left}"
              f"\n\tMessages: [{messages}]"
              f"\n\tImages: [{image_names}]")
    else:
        print(f"\n\n\nUser: {user.username} {user.user_id}, Messages: [{messages}], Images: []")


async def fetch_premium_purchases_by_user_id(user_id: int) -> list:
    """
    Fetch premium purchase records for a user.

    :param user_id: The user's ID.
    :return: A list of dictionaries, each representing a premium purchase.
    """
    async with AsyncSession(async_engine) as session:
        result = await session.execute(
            select(PremiumPurchase)
            .where(PremiumPurchase.user_id == user_id)
            .order_by(PremiumPurchase.purchase_date)
        )
        purchases = result.scalars().all()
        return [
               {"purchase_date": purchase.purchase_date.strftime(DATEFORMAT),
                "expiration_date": purchase.expiration_date.strftime(DATEFORMAT),
                "targets_increment": purchase.targets_increment,
                "request_increment": purchase.request_increment} for purchase in purchases]


async def return_user(user: User) -> Dict[str, Union[int, str, bool, Column]]:
    """
    Return user data as a dictionary.

    :param user: The User object.
    :return: A dictionary containing user data.
    """
    return {'user_id': user.user_id, 'username': user.username, 'first_name': user.first_name,
            'last_name': user.last_name, 'mode': user.mode, 'status': user.status,
            'requests_left': user.requests_left, 'targets_left': user.targets_left,
            'premium_expiration': {user.premium_expiration}}


async def fetch_all_user_ids() -> List[int]:
    """
    Fetch all user tg IDs from the database.

    :return: A list of user tg IDs.
    """
    async with AsyncSession(async_engine) as session:
        result = await session.execute(select(User.user_id))
        user_ids = result.scalars().all()
        return user_ids


async def fetch_all_users_data() -> None:
    """
    Print data for all users to console.

    :return: None
    """
    all_user_ids = await fetch_all_user_ids()
    for user_id in all_user_ids:
        await fetch_user_data(user_id)


async def update_photo_timestamp(user_id: int, timestamp: datetime) -> None:
    """
    Update the last photo sent timestamp for a user to prevent them from sending multiple photos at once.

    :param user_id: The tg ID of the user.
    :param timestamp: The timestamp to set.
    :return: None
    """
    async with AsyncSession(async_engine) as session:
        async with session.begin():
            user = await session.execute(select(User).filter_by(user_id=user_id))
            user = user.scalar_one_or_none()
            if user:
                user.last_photo_sent_timestamp = timestamp
            await session.commit()


async def toggle_receive_target_flag(user_id: int, flag: int = 0):
    """
    Switches states for target images to put faces onto. Available only for premium users.
    :param user_id: The tg ID of the user.
    :param flag: on-off value to switch the flag
    :return: None
    """
    async with AsyncSession(async_engine) as session:
        async with session.begin():
            user = await session.execute(select(User).filter_by(user_id=user_id))
            user = user.scalar_one_or_none()
            if user:
                user.receive_target_flag = flag
                await session.commit()


async def fetch_user_by_id(user_id: int) -> Optional[User]:
    """
    Fetch a user by their tg ID.

    :param user_id: The tg ID of the user to fetch.
    :return: The User object or None if not found.
    """
    async with AsyncSession(async_engine) as session:
        result = await session.execute(select(User).filter_by(user_id=user_id))
        user = result.scalar_one_or_none()
        return user


async def update_user_quotas(free_requests: int = 10, td: int = HOUR_INTERVAL) -> None:
    """
    Update user quotas based on their status.

    :param free_requests: The number of requests for free users.
    :param td: time interval to check.
    :return: None
    """

    async with AsyncSession(async_engine) as session:
        async with session.begin():
            # Fetch all users
            users = await session.execute(select(User))
            users = users.scalars().all()

            for user in users:
                await session.execute(delete(PremiumPurchase).where(
                        (PremiumPurchase.user_id == user.user_id) &
                        (PremiumPurchase.expiration_date < datetime.now().date())))

                # Fetch all active premium purchases for the user
                active_purchases = await session.execute(select(PremiumPurchase).where(
                        PremiumPurchase.user_id == user.user_id,
                        PremiumPurchase.expiration_date >= datetime.now().date()))
                active_purchases = active_purchases.scalars().all()

                if active_purchases:
                    # Calculate the total increments for requests and targets from active purchases
                    total_request_increment = sum(purchase.request_increment for purchase in active_purchases)
                    total_targets_increment = sum(purchase.targets_increment for purchase in active_purchases)

                    # Update the user's quotas based on the total increments from active purchases
                    user.requests_left = total_request_increment
                    user.targets_left = total_targets_increment
                else:
                    # No active premium purchases - revert the user to free status
                    user.status = 'free'
                    user.requests_left = free_requests
                    user.premium_expiration = None  # Clear the premium expiration date
            await session.commit()
    await log_scheduler_run("update_user_quotas", "success", "Completed updating user quotas", td)


async def fetch_scheduler_logs(job_name: str = None):
    """
    Fetches entries from the scheduler_logs table, optionally filtered by a specific job name.

    :param job_name: Optional. The name of the job to filter logs by.
    :return: A list of dictionaries containing log entries.
    """
    async with AsyncSession(async_engine) as session:
        async with session.begin():
            if job_name:
                query = select(SchedulerLog).where(SchedulerLog.job_name == job_name).order_by(
                    SchedulerLog.run_datetime.desc())
            else:
                query = select(SchedulerLog).order_by(SchedulerLog.run_datetime.desc())
            result = await session.execute(query)
            logs = result.scalars().all()

            log_entries = [
                {"id": log.id,
                 "job_name": log.job_name,
                 "run_datetime": log.run_datetime,
                 "status": log.status,
                 "details": log.details}
                for log in logs]
            print(log_entries)
            return log_entries


async def add_premium_purchase_for_premium_users():
    """
    Add a PremiumPurchase instance for every user currently marked as premium.
    """
    async with AsyncSession(async_engine) as session:
        async with session.begin():
            premium_users = await session.execute(
                select(User).where(User.status == 'premium')
            )
            premium_users = premium_users.scalars().all()

            for user in premium_users:
                purchase_date = datetime.now()
                expiration_date = purchase_date + timedelta(days=PREMIUM_DAYS)

                new_purchase = PremiumPurchase(
                    user_id=user.user_id,
                    purchase_date=purchase_date,
                    expiration_date=expiration_date,
                    targets_increment=10,
                    request_increment=100)

                session.add(new_purchase)


async def remove_expired_premium_purchases(user_id: int):
    """
    Remove expired PremiumPurchase entries for a specific user_id.

    :param user_id: The ID of the user whose expired PremiumPurchases should be removed.
    """
    async with AsyncSession(async_engine) as session:
        async with session.begin():
            await session.execute(delete(PremiumPurchase).where(
                                                            (PremiumPurchase.user_id == user_id) &
                                                            (PremiumPurchase.expiration_date < datetime.now().date())))
            await session.commit()
