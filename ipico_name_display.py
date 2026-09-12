import tkinter as tk
import tkinter.font as tkfont
import random
import socket
import re
from threading import Thread, Event
import subprocess
import traceback
import csv
import time
from datetime import datetime
import sys

# ==========================================
# CONFIGURATION
# ==========================================
# https://supportfileshare.active.com/Support/IPICO/Developers-Guide-Manual--2-3-2.pdf

READ_ALOUD = True           # Enables text-to-speach to pronounce participnats names (requires pyttsx3)
MOCK_IPICO = False           # Use fake date from the participants file (True) or the actual device (False)
READER_IP = "192.168.0.54"  # IP Address of the IPico device
READER_PORT = 10000         # Raw data port of the IPico device
PACKET_SIZE = 38            # 34 ASCII-hex bytes + \r\n (2 bytes)


numtag_file      = 'numtag.csv'       # path to csv file with the first row header: [num,tag]
participants_file= 'participants.csv' # path to csv file with the first row header: [First Name,Last Name,Bib,Gender,Age,Race Group]

MAIN_ALIGNMENT = "center"   # Alignment of the main panel text: "left", "center", or "right"

# ==========================================

# Global state
update_counter = 0
last_displayed_tag = None
update_counter_cache = None
max_history_items = 10     
right_panel_width = 200    
left_panel_width = 800     
max_left_sz = 80           

history_label = None
main_label = None
category_label = None
team_label = None

history_font = None
main_font = None
category_font = None
team_font = None
right_font = None
resize_btn = None

current_tts_process = None # Tracks the background speech process

STOP_EVENT = Event()
socket_thread = None
# Global array to store names as they arrive
names = []
category_label_text = None
client = None
screen_handle = None

incoming_tags = []
participants = {}
bibs={}
bibs_reverse={}

categories = {
 'Male': [],
 'Female': [],
 'Male Master': [],
 'Female Master':  [],
 'Overall': []
}
tag_category_lookup = {}
#position_labels = ['First', 'Second', 'Third']
position_labels = ['First', 'Second']


################################### Participant Functions #############################

def get_participant_category(participant):
    category  = ''
    master = False
    if participant['Gender'] == 'M':
        category = 'Male'
    else:
        category = 'Female'
        
    if int(participant['Age']) >= 40:
        master = True
        
    return [category, master]

def read_participants():
    with open(numtag_file, mode='r', newline='', encoding="utf-8-sig") as f:
        # Initialize DictReader
        reader = csv.DictReader(f)
        print(reader.fieldnames)  # This will print the header names

        for row in reader:
            bibs[row['num']] = row['tag']
            bibs_reverse[row['tag']] = row['num']
        

    with open(participants_file, mode='r', newline='', encoding="utf-8-sig") as f:
        # Initialize DictReader
        reader = csv.DictReader(f)
        
        # Access the field names
        print(reader.fieldnames)  # This will print the header names
        for row in reader:
            print(row)
            if row['Bib'] not in bibs:
                print("ERROR: participant's bib not found in the Tags file: ", row)
            else:
                participants[bibs[row['Bib']]] = row
        
    #print(tags)
    
def get_labels_for_tag_record(tag_record):
    main_text = None
    print(f"[READ] Tag: {tag_record['tag']} | Time: {tag_record['time']}")
    print(int(tag_record['tag'], 16))
    
    if tag_record['tag'] in participants:
        participant = participants[tag_record['tag']]
        # if 'seen_already' not in participant or not participant['seen_already']:
        
        category_text = ''
        main_text = f"{participant['First Name']} {participant['Last Name']} [{participant['Gender']}{participant['Age']}]"
        team_text = participant['Race Group']

        if tag_record['tag'] not in tag_category_lookup:
            # Trigger the Text-to-Speech for the new name
            if READ_ALOUD: read_aloud(f"{participant['First Name']} {participant['Last Name']}")

            categories['Overall'].append(participant)

            [category, master] = get_participant_category(participant)
            
            position = len(categories[category]) + 1
            position_master = len(categories[category + ' Master']) + 1

            if position <= len(position_labels):
                category_text = f"{position_labels[position-1]} {category}"
            elif master:
                category = f"{category} Master"
                if position_master <= len(position_labels):
                    category_text = f"{position_labels[position_master-1]} {category}"
            
            categories[category].append(participant)

            tag_category_lookup[tag_record['tag']] = category_text
        else:
            category_text = tag_category_lookup[tag_record['tag']]

    elif tag_record['tag'] in bibs_reverse:
        category_text = ''
        team_text = ''
        main_text = f"Bib {bibs_reverse[tag_record['tag']]}"
    else:
        category_text = ''
        team_text = ''
        main_text = f"Tag {tag_record['tag']}"
    return [main_text, category_text, team_text]

def get_summary_label_for_tag_record(tag_record):
    return get_labels_for_tag_record(tag_record)[0]

################################### Text to speach ######################################
def read_aloud(text):
    """
    Reads text out loud using a background subprocess.
    If a previous speech process is still running, it terminates it immediately.
    """
    global current_tts_process
    
    # If the previous voice is still talking, kill the process to interrupt it
    if current_tts_process and current_tts_process.poll() is None:
        current_tts_process.terminate()
        
    # A tiny standalone script that pyttsx3 will run in the background
    tts_script = """
import pyttsx3
import sys
engine = pyttsx3.init()
engine.say(sys.argv[1])
engine.runAndWait()
"""
    # Spawn the subprocess using the same python executable running this app
    # Passing the text via sys.argv avoids any quote-escaping issues
    current_tts_process = subprocess.Popen([sys.executable, "-c", tts_script, text])

################### GUI Functions #####################################################


def trim_line(text, font, max_width):
    """Trims text and adds '...' if it exceeds the max pixel width."""
    if max_width <= 0:
        return ""
        
    if font.measure(text) <= max_width:
        return text
        
    ellipsis = "..."
    if font.measure(ellipsis) > max_width:
        return ""
        
    # Iteratively trim characters until it fits with the ellipsis
    while len(text) > 0 and font.measure(text + ellipsis) > max_width:
        text = text[:-1]
        
    return text + ellipsis

def shrink_to_fit(text, font, base_size, max_width, min_size=10):
    """Iteratively shrinks a font size until the text fits within max_width."""
    current_sz = base_size
    font.config(size=current_sz)
    
    while font.measure(text) > max_width and current_sz > min_size:
        current_sz -= 2
        font.config(size=current_sz)

def update_display():
    global last_displayed_tag
    global update_counter

    update_counter = update_counter + 1
    """Updates the labels with the latest array data and constraints."""
    if not incoming_tags:
        return
        
    current_index = len(incoming_tags)
    current_record = incoming_tags[-1]
    last_displayed_tag = current_record['tag']

    # 1. Prepare texts for the Left Panel labels
    [main_text, category_text, team_text] = get_labels_for_tag_record(current_record)

    if category_text != '':
        cat_color = "#ff4444"  # Red
        category_text = f"{current_index}: {category_text}"
    else:
        cat_color = "gray"     # Default gray
        category_text = f"{current_index}"
        
    
    

    
    # Calculate base sizes for the 3 labels relative to the max_left_sz
    main_base_sz = max_left_sz
    cat_base_sz = max(10, int(max_left_sz * 0.3))   
    team_base_sz = max(10, int(max_left_sz * 0.5))  
    
    # Shrink each font dynamically if the text is too long for the panel
    shrink_to_fit(main_text, main_font, main_base_sz, left_panel_width)
    shrink_to_fit(category_text, category_font, cat_base_sz, left_panel_width)
    shrink_to_fit(team_text, team_font, team_base_sz, left_panel_width)
    
    # Update the UI Labels
    main_label.config(text=main_text)
    category_label.config(text=category_text, fg=cat_color)
    team_label.config(text=team_text)
    
    # 2. Update the smaller history label in the right panel
    recent_previous = incoming_tags[:-1][::-1][:max_history_items]
    
    history_list = []
    start_idx = len(incoming_tags) - 1 
    
    for i, record in enumerate(recent_previous):
        orig_idx = start_idx - i
        summary_label = get_summary_label_for_tag_record(record)
        line = f"{orig_idx}: {summary_label}"
        
        # Ensure the string fits on a single line within the right panel
        trimmed_line = trim_line(line, right_font, right_panel_width)
        history_list.append(trimmed_line)
    
    history_text = "\n".join(history_list)
    history_label.config(text=history_text)
    return update_counter

def apply_layout(w, h):
    """Calculates fonts and capacities based on provided width and height."""
    global max_history_items, right_panel_width, left_panel_width, max_left_sz
    
    if w < 100 or h < 100:  
        return
        
    # --- Left Panel Math ---
    # We take 90% of the left panel's physical width to leave safe padding 
    # regardless of whether the text is anchored left, center, or right.
    left_panel_width = (w * 0.8) * 0.9
    
    # Determine the maximum possible font size based on window height/width ratio
    max_left_sz = max(12, int(min(w * 0.8 * 0.08, h * 0.15)))
    
    # --- Right Panel Math ---
    right_sz = max(8, int(min(w * 0.2 * 0.08, h * 0.04)))
    right_font.config(size=right_sz)
    
    line_space = right_font.metrics("linespace")
    if line_space > 0:
        max_history_items = max(1, int(h / line_space)) - 1 
        
    right_panel_width = (w * 0.2) - 20
    
    update_display()

def on_resize(event):
    """Fires whenever the window size changes."""
    if event.widget == screen_handle:
        apply_layout(event.width, event.height)

def toggle_screen_size():
    """Toggles between 1/4 screen and full screen."""
    is_fullscreen = screen_handle.attributes("-fullscreen")
    
    if is_fullscreen:
        # We are fullscreen, so shrink to 1/4 screen
        screen_handle.attributes("-fullscreen", False)
        
        screen_width = screen_handle.winfo_screenwidth()
        screen_height = screen_handle.winfo_screenheight()
        
        new_width = screen_width // 2
        new_height = screen_height // 2
        x = (screen_width - new_width) // 2
        y = (screen_height - new_height) // 2
        
        screen_handle.geometry(f"{new_width}x{new_height}+{x}+{y}")
        resize_btn.config(text="Full Screen")
    else:
        # We are windowed, maximize to fullscreen
        screen_handle.attributes("-fullscreen", True)
        resize_btn.config(text="1/4 Screen")

def setup_display():
    global screen_handle
    global history_label
    global main_label
    global category_label
    global team_label

    global history_font
    global main_font
    global category_font
    global team_font
    global right_font

    global resize_btn

    # --- UI Setup ---
    screen_handle = tk.Tk()
    screen_handle.title("Incoming Records Display")

    # Determine initial 1/4 screen dimensions and placement
    screen_w = screen_handle.winfo_screenwidth()
    screen_h = screen_handle.winfo_screenheight()
    init_w = screen_w // 2
    init_h = screen_h // 2
    init_x = (screen_w - init_w) // 2
    init_y = (screen_h - init_h) // 2

    # Apply default 1/4 screen geometry
    screen_handle.geometry(f"{init_w}x{init_h}+{init_x}+{init_y}")

    main_font = tkfont.Font(family="Helvetica", weight="bold")
    category_font = tkfont.Font(family="Helvetica", weight="bold")
    team_font = tkfont.Font(family="Helvetica", slant="italic")
    right_font = tkfont.Font(family="Helvetica")

    screen_handle.bind("<Escape>", lambda event: stop_application(event))
    screen_handle.bind("<q>", lambda event: stop_application(event))
    screen_handle.bind("<Configure>", on_resize)

    # --- Layout: Two Panes ---
    left_frame = tk.Frame(screen_handle, bg="black")
    left_frame.place(relx=0, rely=0, relwidth=0.8, relheight=1.0)

    right_frame = tk.Frame(screen_handle, bg="#1a1a1a")
    right_frame.place(relx=0.8, rely=0, relwidth=0.2, relheight=1.0)

    # --- Top Left Buttons ---
    button_frame = tk.Frame(left_frame, bg="black")
    button_frame.place(x=20, y=20, anchor="nw")

    # Button starts in windowed mode, so it offers "Full Screen"
    resize_btn = tk.Button(button_frame, text="Full Screen", command=toggle_screen_size, font=("Helvetica", 12))
    resize_btn.pack(side="left", padx=(0, 10)) 

    exit_btn = tk.Button(button_frame, text="Exit", command=screen_handle.destroy, font=("Helvetica", 12))
    exit_btn.pack(side="left")


    # --- Alignment Mapping Logic ---
    # Dictates how Tkinter places the container and aligns the text inside it
    align_map = {
        "left":   {"relx": 0.05, "anchor": "w",      "justify": "left"},
        "center": {"relx": 0.50, "anchor": "center", "justify": "center"},
        "right":  {"relx": 0.95, "anchor": "e",      "justify": "right"}
    }

    # Fallback to 'center' if the configuration string is invalid
    conf = align_map.get(MAIN_ALIGNMENT.lower(), align_map["center"])

    # --- Central Text Display (Left Panel) ---
    center_frame = tk.Frame(left_frame, bg="black")
    center_frame.place(relx=conf["relx"], rely=0.5, anchor=conf["anchor"])

    category_label = tk.Label(center_frame, font=category_font, fg="gray", bg="black", justify=conf["justify"])
    category_label.pack(anchor=conf["anchor"], pady=(0, 100))

    main_label = tk.Label(center_frame, font=main_font, fg="white", bg="black", justify=conf["justify"])
    main_label.pack(anchor=conf["anchor"], pady=(0, 100))

    team_label = tk.Label(center_frame, font=team_font, fg="#aaaaaa", bg="black", justify=conf["justify"])
    team_label.pack(anchor=conf["anchor"])


    # --- History Display (Right Panel) ---
    history_label = tk.Label(right_frame, font=right_font, fg="gray", bg="#1a1a1a", justify="left")
    history_label.place(x=10, y=10, anchor="nw")

    # --- INITIALIZATION ---
    screen_handle.update_idletasks()
    # Use the calculated 1/4 dimensions for the initial layout sizing
    apply_layout(init_w, init_h)



################### IPico Socket Functions #####################################################

def calculate_ipico_lrc(packet_bytes: bytes) -> str:
    """Calculates the 8-bit LRC checksum modulo 256 for bytes 2 through 33."""
    # LRC drops the frame header (first 2 bytes 'aa') and the trailing checksum/delimiters
    lrc_sum = sum(packet_bytes[2:34])
    return f"{lrc_sum & 0xFF:02x}"
    
def parse_ipico_packet(packet_bytes):
    """
    Parses a 36-byte ASCII-hex IPICO Lite Reader packet.
    Expected format outline:
    - aa (Header)
    - Reader ID (2 hex chars)
    - Tag ID (12 hex chars)
    - Antenna/Channels (4 hex chars)
    - Date/Time BCD (14 hex chars)
    - Checksum LRC (2 hex chars)
    - \r\n (2 trailing bytes)
    """
    try:
        # Decode bytes to a string and strip the trailing carriage return/line feed
        packet_str = packet_bytes.decode('ascii').strip()
       
        # Validation checks
        if not packet_str.startswith('aa'):
            return None
        
        # Extract fields based on string slicing indices
        header    = packet_str[0:2]
        reader_id = packet_str[2:4]
        tag_id    = packet_str[4:16]      # 12-digit unique Chip ID
        antenna   = packet_str[16:20]     # Channel info
        bcd_time  = packet_str[20:34]     # YYMMDDHHMMSS + 10ms fractions
        rcvd_lrc  = packet_str[34:36]
        ms        = int(bcd_time[12:14], 16)
        
        # Verify Checksum matching
        calc_lrc = calculate_ipico_lrc(packet_bytes)
        if rcvd_lrc != calc_lrc:
            print(f"[Warning] LRC Checksum Failed. Expected: {calc_lrc}, Got: {rcvd_lrc}")
            return None
        
        # Prettify the timestamp for display (Optional)
        formatted_time = f"20{bcd_time[0:2]}-{bcd_time[2:4]}-{bcd_time[4:6]} {bcd_time[6:8]}:{bcd_time[8:10]}:{bcd_time[10:12]}.{ms}"

        return {
            "Tag ID": tag_id.upper(),
            "Time": formatted_time,
            "Antenna/Info": antenna,
            "Reader ID": reader_id
        }
    except Exception as e:
        print(f"Error parsing packet: {e}")
        return None

def mock_ipico_socket():
    """Mocks a network socket receiving structured data."""
    if STOP_EVENT.is_set():
        print('The ipico mock thread was stopped, exiting.')
        return
    
    mock_data = list(participants.keys()) + list(bibs_reverse.keys())[:5]
    
    new_tag = random.choice(mock_data)
    if new_tag not in [incoming_tag['tag'] for incoming_tag in incoming_tags]:
        incoming_tags.append({
            'tag': new_tag,
            'time': datetime.now().strftime("%H:%M:%S")
        })
    
        if last_displayed_tag != new_tag: update_display()
    
    # Schedule the next record to arrive
    screen_handle.after(1500, mock_ipico_socket)

def listen_ipico_socket():
    global client
    global category_label_text
    global names
    # Initialize socket client
    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    
    client.settimeout(None)
    #client.setblocking(False)
    
    print(f"Connecting to IPICO Lite Reader at {READER_IP}:{READER_PORT}...")
    try:
        client.connect((READER_IP, READER_PORT))
        print("Connected! Listening for tag streams...")
    except Exception as e:
        print(f"Connection failed: {e}")
        stop_display()
        return

    data_buffer = b""
    
    try:
        while True:
            if STOP_EVENT.is_set():
                print('The ipico thread was stopped, exiting.')
                client.close()
                return
            # Read streaming data chunk from socket
            chunk = client.recv(1024)
            if not chunk:
                print("Reader closed the connection.")
                break
                
            data_buffer += chunk
            # Process as many 36-byte blocks as available in the stream buffer
            while len(data_buffer) >= PACKET_SIZE:
                # Isolate the current 36-byte segment
                packet = data_buffer[:PACKET_SIZE]
                # Check for standard trailing delimiters to maintain alignment
                if packet.endswith(b'\r\n'):
                    parsed_data = parse_ipico_packet(packet)
                    if parsed_data:
                        try:
                            new_record = {'tag': parsed_data['Tag ID'], 'time': parsed_data['Time']}
                            if new_record['tag'] not in [incoming_tag['tag'] for incoming_tag in incoming_tags]:
                                incoming_tags.append(new_record)
                         
                                if last_displayed_tag != new_record['tag']: update_display()
                                
                        except Exception:
                            traceback.print_exc()
                            
                    # Advance buffer by 36 bytes
                    data_buffer = data_buffer[PACKET_SIZE:]
                else:
                    # Alignment issue: sync back up by hunting for next 'aa' header
                    print("Packet alignment lost. Re-syncing stream...")
                    sync_index = data_buffer.find(b'aa', 1)
                    if sync_index != -1:
                        data_buffer = data_buffer[sync_index:]
                    else:
                        data_buffer = b""
                        break
                        
    except KeyboardInterrupt:
        print("\nStopping data collection.")
    except:
        stop_display()
    finally:
        client.close()
        print("Socket disconnected.")
    print('Exit socket loop')
    return True
    
def create_socket_loop():
    global socket_thread
    global MOCK_IPICO
    # Create a thread for the socket function
    socket_thread = Thread(target= listen_ipico_socket if not MOCK_IPICO else mock_ipico_socket, daemon=True)#, daemon=True

    # Start the thread
    socket_thread.start()

    # Optionally, wait for the thread to finish
    #thread.join()

def set_ipico_stop_event():
    print("Stop event")
    STOP_EVENT.set()
    if client: 
        print("we have client")
        try:
            print('Stopping client')
            client.shutdown(socket.SHUT_WR)
        except:
            pass

################### General / Threading Functions #####################################################

def stop_text_to_speach_threads():
    global current_tts_process
    if current_tts_process and current_tts_process.poll() is None:
        current_tts_process.terminate()
    
def stop_display():
    stop_text_to_speach_threads()
    screen_handle.destroy()

def stop_application(event=None):
    stop_text_to_speach_threads()
    set_ipico_stop_event()
    stop_display()
    print('exit')
    exit()
    


setup_display()

# Start listening to the socke after 0.5 seconds
read_participants()

screen_handle.after(1000, create_socket_loop)
# screen_handle.after(1000, mock_socket_receive)

screen_handle.mainloop()
