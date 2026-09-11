import tkinter as tk
import random
import socket
import re
from threading import Thread, Event
import traceback
import csv
import time

# https://supportfileshare.active.com/Support/IPICO/Developers-Guide-Manual--2-3-2.pdf
# --- CONFIGURATION ---
# Default IPICO reader configurations (Adjust to match your reader's setup)

READER_IP = "192.168.0.54"  
READER_PORT = 10000         
PACKET_SIZE = 38            # 34 ASCII-hex bytes + \r\n (2 bytes)
STOP_EVENT = Event()
socket_thread = None
# Global array to store names as they arrive
names = []
category_label_text = None
client = None
screen_handle = tk.Tk()

participants = {}
bibs={}
bibs_reverse={}

numtag_file='numtag.csv'
participants_file='participants.csv'

categories = {
 'Male': [],
 'Female': [],
 'Male Master': [],
 'Female Master':  [],
}
position_labels = ['First', 'Second', 'Third']

def get_participant_category(participant):
    category  = ''
    if participant['Gender'] == 'M':
        category = 'Male'
    else:
        category = 'Female'
        
    if int(participant['Age']) >= 40:
        category = category + ' Master'
        
    return category

def update_display():
    """Updates the labels with the latest array data."""
    if not names:
        return
        
    # The most recent name and its index (1-based)
    current_index = len(names)
    current_name = names[-1]
    current_name = f"{current_index}: {current_name}"
    current_name = current_name.split('|')
    
    team_label_text = ''
    category_label_text = ''
    if len(current_name) == 3:
        [current_name, team_label_text, category_label_text] = current_name
    elif len(current_name) == 2:
        [current_name, team_label_text] = current_name
    else:
        current_name = current_name[0]
    
    # 1. Update the prominent main label
    main_label.config(text=current_name)
    
    # 2. Update the smaller history label below
    # Build a list of formatted strings for previous names
    history_list = []
    for i, name in enumerate(names[:-1]):
        # i starts at 0, so i + 1 gives us the arrival order
        history_list.append(f"{i + 1}: {name}")
    
    # Reverse the list so the most recent 'previous' name is first
    reversed_history = history_list[::-1]
    
    # Take only the first 10 items and join them with newlines
    history_text = "\n".join(name.replace('|', ' ') for name in reversed_history[:10])
    
    # Update the history label text
    history_label.config(text=history_text)

    category_label.config(text=category_label_text)
    
    team_label.config(text=team_label_text)

def toggle_screen_size():
    """Toggles between 1/4 screen and full screen, updating the button text."""
    # Check if we are currently in fullscreen mode
    is_fullscreen = screen_handle.attributes("-fullscreen")
    
    if is_fullscreen:
        # We are in fullscreen, so shrink to 1/4 screen
        screen_handle.attributes("-fullscreen", False)
        
        screen_width = screen_handle.winfo_screenwidth()
        screen_height = screen_handle.winfo_screenheight()
        
        # 1/4 of the screen area means 1/2 of the width and 1/2 of the height
        new_width = screen_width // 2
        new_height = screen_height // 2
        
        # Calculate x and y coordinates to center the window on the screen
        x = (screen_width - new_width) // 2
        y = (screen_height - new_height) // 2
        
        # Apply the new dimensions and placement
        screen_handle.geometry(f"{new_width}x{new_height}+{x}+{y}")
        
        # Update the button text
        resize_btn.config(text="Full Screen")
    else:
        # We are in windowed mode, so maximize back to fullscreen
        screen_handle.attributes("-fullscreen", True)
        
        # Update the button text
        resize_btn.config(text="1/4 Screen")


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

def listen_ipico_socket(stop_event: Event) -> None:
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
                            main_text = None
                            print(f"[READ] Tag: {parsed_data['Tag ID']} | Time: {parsed_data['Time']} | Ant: {parsed_data['Antenna/Info']}")
                            print(int(parsed_data['Tag ID'], 16))
                            
                            if parsed_data['Tag ID'] in participants:
                                participant = participants[parsed_data['Tag ID']]
                                if 'seen_already' not in participant or not participant['seen_already']:
                                    category_label_text = ''
                                    participant['seen_already'] = True
                                    category = get_participant_category(participant)
                                    categories[category].append(participant)
                                    position = len(categories[category])
                                    main_text = f"{participant['First Name']} {participant['Last Name']}|[{participant['Age']} {participant['Gender']} {participant['Race Group']}]"
                                    
                                    if position < 4:
                                        category_label_text = f"{position_labels[position-1]} {category}"
                                        main_text = main_text + '|' + category_label_text

                                        
                                    
                            elif parsed_data['Tag ID'] in bibs_reverse:
                                category_label_text = ''
                                main_text = f"Unknown bib|{bibs_reverse[parsed_data['Tag ID']]}"
                                
                            else:
                                category_label_text = ''
                                main_text = f"Unknown tag|{parsed_data['Tag ID']}"
                            if main_text != None and main_text not in names: names.append(main_text)
                            update_display()
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
    # Create a thread for the socket function
    socket_thread = Thread(target=listen_ipico_socket, args=(STOP_EVENT,), daemon=True)#, daemon=True

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
def stop_display():
    screen_handle.destroy()
    
def stop_application(event=None):
    set_ipico_stop_event()
    stop_display()
    print('exit')
    exit()
    
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
    
# --- UI Setup ---
screen_handle.title("Incoming Names Display")

# Configure full screen initially
screen_handle.attributes("-fullscreen", False)

# Set background to black
screen_handle.configure(bg="black")

# Button to toggle window size
# Placed in the top-left corner
resize_btn = tk.Button(screen_handle, text="Full Screen", command=toggle_screen_size, font=("Helvetica", 12))
resize_btn.place(relx=0.02, rely=0.02, anchor="nw")

# Category Label
category_label = tk.Label(screen_handle, font=("Helvetica", 30, "bold"), fg="red", bg="black")
category_label.place(relx=0.5, rely=0.10, anchor="center")

# Create the label for the prominent current name
main_label = tk.Label(screen_handle, font=("Helvetica", 80, "bold"), fg="white", bg="black")
main_label.place(relx=0.5, rely=0.2, anchor="center")

# Team Label
team_label = tk.Label(screen_handle, font=("Helvetica", 30, "bold"), fg="gray", bg="black")
team_label.place(relx=0.5, rely=0.3, anchor="center")

# Create the label for the history of names
# justify="left" aligns the text to the left side of the text block
history_label = tk.Label(screen_handle, font=("Helvetica", 30), fg="gray", bg="black", justify="left")
history_label.place(relx=0.5, rely=0.45, anchor="n")

# Start listening to the socke after 0.5 seconds
read_participants()

screen_handle.after(1000, create_socket_loop)

# Add a way to exit the application
screen_handle.bind("<Escape>", lambda event: stop_application(event))
screen_handle.bind("<q>", lambda event: stop_application(event))



# Start the GUI event loop
screen_handle.mainloop()


