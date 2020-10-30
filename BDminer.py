#!/Users/schaecher/.pyenv/bin/python3.7
'''
This script was written by Beaker from F3STL. Questions? @srschaecher on twitter or srschaecher@gmail.com.
This script queries Slack for User, Channel, and Conversation (channel) history and then parses all conversations to find Backblasts.
All Backblasts are then parsed to collect the BEATDOWN information for any given workout and puts those attendance records into the AWS F3STL database for recordkeeping.
'''

from slacker import Slacker
from datetime import datetime, timedelta
import pandas as pd
import pytz
import re
import pymysql.cursors
import configparser

# Configure Slack credentials
config = configparser.ConfigParser();
config.read('/Users/schaecher/PycharmProjects/f3Slack/credentials.ini');
key = config['slack']['prod_key']

# Configure AWS Credentials
host = config['aws']['host']
port = int(config['aws']['port'])
user = config['aws']['user']
password = config['aws']['password']
db = config['aws']['db']

# Set Slack tokens
slack = Slacker(key)

#Define AWS Database connection criteria
mydb = pymysql.connect(
    host=host,
    port=port,
    user=user,
    password=password,
    db=db,
    charset='utf8mb4',
    cursorclass=pymysql.cursors.DictCursor)

# Set epoch and yesterday's timestamp for datetime calculations
epoch = datetime(1970, 1, 1)
yesterday = datetime.now() - timedelta(days = 1)
oldest = yesterday.timestamp()

# Make users Data Frame
users_response = slack.users.list()
users = users_response.body['members']
users_df = pd.json_normalize(users)
users_df = users_df[['id', 'profile.display_name', 'profile.real_name']]
users_df = users_df.rename(columns={'id' : 'user_id', 'profile.display_name' : 'user_name', 'profile.real_name' : 'real_name'})
for index, row in users_df.iterrows():
    un_tmp = row['user_name']
    rn_tmp = row['real_name']
    if un_tmp == "" :
        row['user_name'] = rn_tmp

'''
# Get channel list from Slack (note - this has been replaced with a channel list from the AWS database)
channels_response = slack.conversations.list()
channels = channels_response.body['channels']
channels_df = pd.json_normalize(channels)
channels_df = channels_df[['id', 'name', 'created', 'is_archived']]
channels_df = channels_df.rename(columns={'id' : 'channel_id', 'name' : 'channel_name', 'created' : 'channel_created', 'is_archived' : 'archived'})
'''

try:
    with mydb.cursor() as cursor:
        sql = "SELECT channel_id, ao FROM aos WHERE backblast = 1"
        cursor.execute(sql)
        channels = cursor.fetchall()
        channels_df = pd.DataFrame(channels, columns={'channel_id', 'ao'})
finally:
    print('Looking for new Beatdowns from the Slack Backblast posts! Stand by...')
#    mydb.close()

# Get all channel conversation
messages_df = pd.DataFrame([]) #creates an empty dataframe to append to
for id in channels_df['channel_id']:
    response = slack.conversations.history(id)
    messages = response.body['messages']
    temp_df = pd.json_normalize(messages)
    temp_df = temp_df[['user', 'type', 'text', 'ts']]
    temp_df = temp_df.rename(columns={'user' : 'user_id', 'type' : 'message_type', 'ts' : 'timestamp'})
    temp_df["channel_id"] = id
    messages_df = messages_df.append(temp_df, ignore_index=True)

# Calculate Date and Time columns
msg_date = []
msg_time = []
for ts in messages_df['timestamp']:
        seconds_since_epoch = float(ts)
        datetime = epoch + timedelta(seconds=seconds_since_epoch)
        datetime = datetime.replace(tzinfo=pytz.utc)
        datetime = datetime.astimezone(pytz.timezone('America/Chicago'))
        msg_date.append(datetime.strftime('%Y-%m-%d'))
        msg_time.append(datetime.strftime('%H:%M:%S'))
messages_df['date'] = msg_date
messages_df['time'] = msg_time

# Merge the data frames into 1 joined DF
f3_df = pd.merge(messages_df, users_df)
f3_df = pd.merge(f3_df,channels_df)
f3_df = f3_df[['timestamp', 'date', 'time', 'channel_id', 'ao', 'user_id', 'user_name', 'real_name', 'text']]

# Now find only backblast messages (either "Backblast" or "Back Blast") - note .casefold() denotes case insensitivity - and pull out the PAX user ID's identified within
# This pattern finds the Q user ID
pat = r'(?<=\<).+?(?=>)' # This pattern finds username links within brackets <>
bd_df = pd.DataFrame([])
def bd_info():
    # Find the Q information
    qline = re.findall(r'(?<=\n)\*?Q\*?:.+?(?=\n)', str(text_tmp), re.MULTILINE) #This is regex looking for \nQ: with or without an * before Q
    qids = re.findall(pat, str(qline), re.MULTILINE)
    qids = [re.sub(r'@', '', i) for i in qids]
    if qids:
        qid = qids[0]
    else:
        qid = 'NA'
    if len(qids) > 1:
        coqid = qids[1]
    else:
        coqid = 'NA'
    # Find the PAX Count line (if the Q put one in the BB)
    pax_count = re.search(r'(?<=\n)\*?(?i)Count\*?:\*?.+?(?=\n)', str(text_tmp))
    if pax_count:
        pass
    else:
        pax_count = re.search(r'(?<=\n)\*?(?i)Total\*?:\*?.+?(?=\n)', str(text_tmp))
    if pax_count:
        pax_count = pax_count.group()
        pax_count = re.findall('\d+', str(pax_count))
        if pax_count:
            pax_count = int(pax_count[0])
        else:
            pax_count = 0
    fngline = re.findall(r'(?<=\n)\*?FNGs\*?:\*?.+?(?=\n)', str(text_tmp), re.MULTILINE)  # This is regex looking for \nFNGs: with or without an * before Q
    if fngline:
        fngline = fngline[0]
        fngs = re.sub('\*?FNGs\*?:\s?', '', str(fngline))
        fngs = fngs.strip()
    else:
        fngs = 'NA'
    if isinstance(pax_count, int):
        pass
    else:
        pax_count = -1
    global bd_df
    new_row = {'ao_id' : ao_tmp, 'date' : date_tmp, 'q_user_id' : qid, 'coq_user_id' : coqid, 'pax_count' : pax_count, 'backblast' : text_tmp, 'fngs' : fngs}
    bd_df = bd_df.append(new_row, ignore_index = True)

# Iterate through the new bd_df dataframe, pull out the channel_name, date, and text line from Slack. Process the text line to find the beatdown info
for index, row in f3_df.iterrows():
    ao_tmp = row['channel_id']
    date_tmp = row['date']
    text_tmp = row['text']
    text_tmp = re.sub('\*', '', text_tmp, re.MULTILINE)
    if re.findall('^Backblast', text_tmp, re.IGNORECASE|re.MULTILINE):
        bd_info()
    elif re.findall('^Back blast', text_tmp, re.IGNORECASE|re.MULTILINE):
        bd_info()
    elif re.findall('^\*Backblast', text_tmp, re.IGNORECASE|re.MULTILINE):
        bd_info()
    elif re.findall('^\*Back blast', text_tmp, re.IGNORECASE|re.MULTILINE):
        bd_info()
    text_tmp = re.sub('\*', '', text_tmp, re.MULTILINE)
# Now connect to the AWS database and insert some rows!
try:
    with mydb.cursor() as cursor:
        for index, row in bd_df.iterrows():
            sql = "INSERT IGNORE into beatdowns (ao_id, bd_date, q_user_id, coq_user_id, pax_count, backblast, fngs) VALUES (%s, %s, %s, %s, %s, %s, %s)"
            ao_id = row['ao_id']
            bd_date = row['date']
            q_user_id = row['q_user_id']
            coq_user_id = row['coq_user_id']
            pax_count = row['pax_count']
            backblast = row['backblast']
            fngs = row['fngs']
            val = (ao_id, bd_date, q_user_id, coq_user_id, pax_count, backblast, fngs)
            cursor.execute(sql, val)
            mydb.commit()
            if cursor.rowcount == 1:
                print(cursor.rowcount, "records inserted.")
                print('Date: ', bd_date)
                print('AO: ', ao_id)
                print('Q: ', q_user_id)
                print('Co-Q', coq_user_id)
                print('Pax Count:',pax_count)
                print('fngs:', fngs)
                print('--------end--------')
            #Add the Q to the bd_attendance table as some Q's are forgetting to add themselves to the PAX line
            if q_user_id == 'NA':
                pass
            else:
                sql2 = "INSERT IGNORE into bd_attendance (user_id, ao_id, date) VALUES (%s, %s, %s)"
                user_id = row['q_user_id']
                ao_id = row['ao_id']
                date = row['date']
                val2 = (user_id, ao_id, date)
                cursor.execute(sql2, val2)
                mydb.commit()
                if cursor.rowcount == 1:
                    print(cursor.rowcount, "records inserted.")
                    print('PAX: ', user_id)
                    print('AO: ', ao_id)
                    print('Date: ', date)
                    print('--------end--------')
        sql3 = "UPDATE beatdowns SET coq_user_id=NULL where coq_user_id = 'NA'"
        cursor.execute(sql3)
        mydb.commit()
finally:
    mydb.close()
print('Finished. You may go back to your day.')