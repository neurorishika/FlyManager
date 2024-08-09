import serial
import serial.tools.list_ports

def get_available_ports():
    """
    Get the available serial ports.
    Returns:
        ports: list of serial ports
        devices: list of device names
        descs: list of descriptions
    """
    ports = serial.tools.list_ports.comports()
    return ports

def get_next_scan(port_index, ports, baudrate=9600):
    """
    Get the next scan from the serial port.
    """
    # open the port
    port_device = ports[port_index].device
    port = serial.Serial(port_device, baudrate)
    # loop to passively listen to the port (until a carriage return is received)
    keystrokes = []
    while True:
        try: # try to read the port, unless a keyboard interrupt is received or kill signal is sent
            data = port.read().decode("utf-8")
            if data:
                keystrokes.append(data)
                if data == "\r":
                    output = "".join(keystrokes)
                    break
        except KeyboardInterrupt:
            break
    # close the port
    port.close()
    return output

    
