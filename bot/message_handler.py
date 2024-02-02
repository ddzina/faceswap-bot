import aiohttp
import io
import json
import random
import os
import yaml

from datetime import datetime, timedelta
from aiogram import F
from aiogram.filters.command import Command
from aiogram.types import FSInputFile, Message, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
from PIL import Image
from bot.db_requests import set_requests_left, update_user_mode, log_input_image_data, exist_user_check, \
                            log_output_image_data, log_text_data, fetch_user_data, fetch_all_users_data, \
                            decrement_requests_left, buy_premium, update_photo_timestamp, receive_user, \
                            toggle_receive_target_flag

buttonname1 = 'Peter the Great'
buttonname2 = 'Catherine the Great'

buttonname3 = 'Mona Lisa'
buttonname4 = 'Count Stroganoff'

buttonname5 = 'Emperor of Mankind'
buttonname6 = 'Adeptus Sororitas'

buttonname7 = 'Cyberboy'
buttonname8 = 'Cybergirl'


buttonname9 = 'Anime boy'
buttonname10 = 'Anime girl'

buttonname11 = 'Ken'
buttonname12 = 'Barbie'
buttons = [buttonname1, buttonname2, buttonname3, buttonname4,
           buttonname5, buttonname6, buttonname7, buttonname8,
           buttonname9, buttonname10, buttonname11, buttonname12]

DELAY_BETWEEN_IMAGES = 2
SENT_TIME = {}


async def get_contacts():
    with open('bot/contacts.yaml', 'r') as f:
        config = yaml.safe_load(f)
    return config


async def generate_filename(folder='original'):
    while True:
        filename = os.path.join('temp/'+folder, f'img_{random.randint(100, 999999)}.png')
        if not os.path.exists(filename):
            return os.path.join(os.getcwd(), filename)


async def get_n_name(name, n):
    return f'{name[:-4]}_{n}.png'


async def handle_start(message):
    await exist_user_check(message)
    await message.answer("Welcome! Send me a photo of a person and I will return their face.")
    await handle_source_command(message)


async def handle_support(message):
    user = await user_contacts(message.from_user)
    contact = await get_contacts()
    await message.bot.send_message(contact['my_id'], f'User: {user} requires help')
    await message.answer("Request has been sent to the administrator. You'll be contacted. Probably")


async def user_contacts(m):
    return f'ids:{m.id} @{m.username} {m.url}\nname: {m.first_name} {m.last_name} {m.full_name}'


async def handle_contacts(message):
    contacts = await get_contacts()
    await message.answer(f"Reach me out through:\nTelegram: @{contacts['telegram']}\nGithub: {contacts['github']}\n")


async def donate_link(message):
    bthash = await get_contacts()
    response_message = "Support me with BTC:"
    await message.answer(response_message)
    await message.answer(bthash['cryptohash'])


async def handle_help(message):
    help_message = (
        "This bot can only process photos that have people on it. Here are the available commands:\n"
        "/start - Start the bot\n"
        "/help - Display this help message\n"
        "/status - Check your account limits\n"
        "/pictures - Select a target picture\n"
        "/reset_limit - Reset your image limit to 10\n"
        "/buy_premium - Add 100 images and set account to premium\n"
        "/contacts - Show contacts list\n"
        "/support - Send a support request\n"
        "/donate - Support me\n"
        "Send me a photo, and I'll process it!")
    await message.answer(help_message)


async def save_img(img, img_path):
    orig = Image.open(io.BytesIO(img))
    orig.save(img_path, format='PNG')


async def check_limit(user, message):
    if user.requests_left <= 0:
        await message.answer("Sorry, you are out of attempts. Try again later")
        return False
    return True


async def check_time_limit(user, message, n_time=20):
    if user.status == 'premium':
        return True
    if (datetime.now() - user.last_photo_sent_timestamp) < timedelta(seconds=n_time):
        secs = (datetime.now() - user.last_photo_sent_timestamp).total_seconds()
        text = f"Sorry, too many requests. Please wait {n_time-int(secs)} more seconds"
        await message.answer(text)
        return False
    return True


async def prevent_multisending(message):
    dt = datetime.now()
    last_sent = SENT_TIME.get(message.from_user.id, None)
    if message.media_group_id is None and (last_sent is None or (
            dt - last_sent).total_seconds() >= DELAY_BETWEEN_IMAGES):
        SENT_TIME[message.from_user.id] = dt
        return True
    return False


async def show_target_pictures(message):
    output_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + '\\temp\\target_images\\collage.png'
    inp_file = FSInputFile(output_path)
    await message.answer_photo(photo=inp_file)


async def handle_image(message: Message, token):
    await exist_user_check(message)
    user = await fetch_user_data(message.from_user.id)
    if not (await check_limit(user, message) and await check_time_limit(user, message)):
        return

    await update_photo_timestamp(user.user_id, datetime.now())

    face_extraction_url = 'http://localhost:8000/insighter'
    file_path = await message.bot.get_file(message.photo[-1].file_id)
    file_url = f"https://api.telegram.org/file/bot{token}/{file_path.file_path}"
    input_path = await generate_filename('target_images' if user.receive_target_flag else 'original')
    await log_input_image_data(message, input_path)

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(file_url) as response:
                if response.status == 200:
                    print('Image downloaded')
                    await message.answer('Image received. Processing...')
                    content = await response.read()
                    await save_img(content, input_path)
                    if await target_image_check(user, input_path):
                        await message.answer('Target image uploaded, send your source image')
                        return
                else:
                    error_message = await response.text()
                    print(error_message)
                    await message.answer('Failed to download image. Please try again')
                    return


            async with session.post(face_extraction_url,
                                    data={'file_path': input_path,
                                          'mode': user.mode}
                                    ) as response:
                print('Sending image path through fastapi')

                if response.status == 200:
                    image_data_list = await response.text()
                    image_paths = json.loads(image_data_list)
                    await log_output_image_data(message, input_path, image_paths)   # logging to db

                    for output_path in image_paths:
                        user = await fetch_user_data(message.from_user.id)
                        if not (await check_limit(user, message)):
                            return
                        inp_file = FSInputFile(output_path)
                        await message.answer_photo(photo=inp_file)
                        print('Image sent')

                    await decrement_requests_left(user.user_id, n=len(image_paths))
                    limit = max(0, user.requests_left - len(image_paths))
                    await message.answer(f'You have {limit} attempts left')

                else:
                    error_message = await response.text()
                    print(error_message)
                    await message.answer('Failed to process image. Please try again')
                    return

    except Exception as e:
        print(e)    # TODO: log it
        await message.answer('Sorry must have been an error. Try again later.')


async def handle_text(message: Message):
    await exist_user_check(message)
    await log_text_data(message)
    response_text = (
        "I'm currently set up to process photos only. "
        "Please send me a photo of a person, and I will return their face.")
    await fetch_user_data(message.from_user.id)
    await message.answer(response_text)


async def handle_unsupported_content(message: Message):
    await message.answer("Sorry, I can't handle this type of content.\n"
                         "Please send me a photo from your gallery, and I will return the face of a person on it.")


async def output_all_users_to_console(*args):
    await fetch_all_users_data()


async def handle_source_command(message: Message):
    button_1 = InlineKeyboardButton(text=buttonname1, callback_data='1')
    button_2 = InlineKeyboardButton(text=buttonname2, callback_data='2')
    button_3 = InlineKeyboardButton(text=buttonname3, callback_data='3')
    button_4 = InlineKeyboardButton(text=buttonname4, callback_data='4')
    button_5 = InlineKeyboardButton(text=buttonname5, callback_data='5')
    button_6 = InlineKeyboardButton(text=buttonname6, callback_data='6')
    button_7 = InlineKeyboardButton(text=buttonname7, callback_data='7')
    button_8 = InlineKeyboardButton(text=buttonname8, callback_data='8')
    button_9 = InlineKeyboardButton(text=buttonname9, callback_data='9')
    button_10 = InlineKeyboardButton(text=buttonname10, callback_data='10')
    button_11 = InlineKeyboardButton(text=buttonname11, callback_data='11')
    button_12 = InlineKeyboardButton(text=buttonname12, callback_data='12')

    keyboard = InlineKeyboardMarkup(inline_keyboard=[[button_1, button_2], [button_3, button_4],
                                                     [button_5, button_6], [button_7, button_8],
                                                     [button_9, button_10], [button_11, button_12]])
    await message.answer("Choose your target image:", reply_markup=keyboard)
    await show_target_pictures(message)


async def button_callback_handler(query: CallbackQuery):
    user_id = query.from_user.id
    await toggle_receive_target_flag(user_id)
    await update_user_mode(user_id, query.data)
    await query.message.answer(f"Target image is {buttons[int(query.data)-1]}\nSend me a photo, and I'll process it!")
    await fetch_user_data(user_id)
    await query.answer()


async def set_user_to_premium(message):
    await exist_user_check(message)
    await buy_premium(message.from_user.id)
    new_number = await fetch_user_data(message.from_user.id)
    await message.answer(f'Congratulations! You got premium status!'
                         f'\nYou have {new_number.requests_left} attempts left')


async def reset_images_left(message):
    await exist_user_check(message)
    n = 10
    await set_requests_left(message.from_user.id, n)
    new_number = await fetch_user_data(message.from_user.id)
    await message.answer(f'You have {new_number.requests_left} attempts left')


async def check_status(message):
    await exist_user_check(message)
    user = await fetch_user_data(message.from_user.id)
    await message.answer(f'Your have a {user.status} account\nYou have {user.requests_left} attempts left')




async def target_image_check(user, input_image):
    if user.status =='premium' and user.receive_target_flag:
        await update_user_mode(user.user_id, input_image)
        await toggle_receive_target_flag(user.user_id)
        return input_image


async def set_receive_flag(message):
    await exist_user_check(message)
    user = await receive_user(message.from_user.id)
    if user.status == 'premium':
        await toggle_receive_target_flag(message.from_user.id, 1)
        await message.answer('Changing set to receiving a new target.\nSend target image.')
        return

    await message.answer('You need to be a premium user for that. Use /buy_premium function')



def setup_handlers(dp, bot_token):
    dp.message(Command('start'))(handle_start)
    dp.message(Command('help'))(handle_help)
    dp.message(Command('contacts'))(handle_contacts)
    dp.message(Command('support'))(handle_support)
    dp.message(Command('show_users'))(output_all_users_to_console)
    dp.message(Command('pictures'))(handle_source_command)
    dp.message(Command('targets'))(show_target_pictures)
    dp.message(Command('new_targets'))(set_receive_flag)
    dp.message(Command('buy_premium'))(set_user_to_premium)
    dp.message(Command('reset_limit'))(reset_images_left)
    dp.message(Command('status'))(check_status)
    dp.message(Command('donate'))(donate_link)
    dp.callback_query()(button_callback_handler)

    async def image_handler(message: Message):
        if await prevent_multisending(message):
            await handle_image(message, bot_token)
            return
        await message.answer("Please send one photo at a time.")

    dp.message(F.photo)(image_handler)
    dp.message(F.text)(handle_text)
    dp.message(F.sticker | F.video | F.document | F.location | F.poll | F.audio | F.voice | F.contact | F.video_note)(
               handle_unsupported_content)
