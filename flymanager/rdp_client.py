from cryptography.fernet import Fernet
import zipfile
import os
import argparse
from split_file_reader import SplitFileReader
from split_file_reader.split_file_writer import SplitFileWriter

def unlock_and_unzip_file(data2unzip, key_dir='key.key',multifile=False):
    # check if key exists
    try:
        with open(key_dir, "rb") as key_file:
            key = key_file.read()
        fernet = Fernet(key)
    except:
        print("Key not found, please generate a key using generate_key(), provide an existing key_dir, or locate the key.")
        return
    
    if not multifile:
        # make sure its a ezip file
        assert data2unzip.split(".")[-1] == "ezip", "data2unzip is not a ezip file under RDP standards"
        # make sure its not multifile
        assert len(data2unzip.split('/')[-1].split(".")) == 2, "data2unzip is a multifile ezip file, please set multifile=True"
        
        # decrypt file
        with open(data2unzip, "rb") as file:
            encrypted_data = file.read()
        decrypted_data = fernet.decrypt(encrypted_data)
        with open(data2unzip.replace("ezip","zip"), "wb") as file:
            file.write(decrypted_data)
        
        # unzip all files and folders
        with zipfile.ZipFile(data2unzip.replace("ezip","zip"), 'r') as zip_ref:
            zip_ref.extractall(data2unzip[:-5])
        
        # delete zip file
        os.remove(data2unzip.replace("ezip","zip"))
    else:
        # make sure its not singlefile
        assert len(data2unzip.split('/')[-1].split(".")) == 3, "data2unzip is a singlefile ezip file, please set multifile=False"
        # make sure its the first ezip file
        assert data2unzip.split('/')[-1].split(".")[1] == "ezip" and data2unzip.split('/')[-1].split(".")[2] == "000", "data2unzip is not a multisplit ezip file under RDP standards"
        
        # get each split file
        split_files = list(filter(lambda x: x.startswith(data2unzip[:-4]), os.listdir()))
        
        # decrypt each split file
        for split_file in split_files:
            with open(split_file, 'rb') as file:
                encrypted = file.read()
            decrypted = fernet.decrypt(encrypted)
            with open(split_file.replace('ezip','zip'), 'wb') as decrypted_file:
                decrypted_file.write(decrypted)
        
        # unzip all files and folders
        with SplitFileReader([files.replace('ezip','zip') for files in split_files]) as sub_zip:
            with zipfile.ZipFile(sub_zip, 'r') as zip_ref:
                zip_ref.extractall(data2unzip[:-9])
        
        # delete zip files
        for split_file in split_files:
            os.remove(split_file.replace('ezip','zip'))


def zip_and_lock_folder(data2zip,key_dir='key.key',multifile=False,split_size_bytes=50_000_000):
    # check if key exists
    try:
        with open(key_dir, "rb") as key_file:
            key = key_file.read()
            fernet = Fernet(key)
    except:
        print("Key not found, please generate a key using generate_key(), provide an existing key_dir, or locate the key.")
        return 
    
    # make sure data2zip is a folder
    assert os.path.isdir(data2zip), "data2zip is not a folder"

    if not multifile:
        # zip folder while preserving directory structure
        with zipfile.ZipFile(f'{data2zip}.zip', 'w') as fullzip: # create zipfile object
            rootlen = len(data2zip) + 1 # get number of characters to remove from each file path
            for folder, subfolders, files in os.walk(f'{data2zip}'): # walk through folders
                for file in files:
                    fn = os.path.join(folder, file)
                    fullzip.write(fn, fn[rootlen:], compress_type = zipfile.ZIP_DEFLATED)
                for subfolder in subfolders:
                    fn = os.path.join(folder, subfolder)
        # encrypt zip file
        with open(f'{data2zip}.zip', 'rb') as file:
            original = file.read()
        encrypted = fernet.encrypt(original)
        with open(f'{data2zip}.ezip', 'wb') as encrypted_file:
            encrypted_file.write(encrypted)
        # delete zip file
        os.remove(f'{data2zip}.zip')
    else:
        # zip folder while preserving directory structure but split into multiple files of max size split_size_bytes
        with SplitFileWriter(f'{data2zip}.zip.',split_size_bytes) as sub_zip:
            with zipfile.ZipFile(sub_zip, 'w') as fullzip:
                rootlen = len(data2zip) + 1 # get number of characters to remove from each file path
                for folder, subfolders, files in os.walk(f'{data2zip}'): # walk through folders
                    for file in files:
                        fn = os.path.join(folder, file)
                        fullzip.write(fn, fn[rootlen:], compress_type = zipfile.ZIP_DEFLATED)
                    for subfolder in subfolders:
                        fn = os.path.join(folder, subfolder)
        # encrypt zip file
        # find all split files
        split_files = list(filter(lambda x: x.startswith(f'{data2zip}.zip.'), os.listdir()))
        # encrypt each split file
        for split_file in split_files:
            with open(split_file, 'rb') as file:
                original = file.read()
            encrypted = fernet.encrypt(original)
            with open(split_file.replace('zip','ezip'), 'wb') as encrypted_file:
                encrypted_file.write(encrypted)
            # delete zip file
            os.remove(split_file)

    
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Lock and unlock files.')
    parser.add_argument('--encrypt', default='', type=str, help='Folder to encrypt.')
    parser.add_argument('--decrypt', default='', type=str, help='File to decrypt.')
    parser.add_argument('--key_dir', default='key.key', type=str, help='Directory of key.')
    parser.add_argument('--multifile', default=False, type=bool, help='Whether to split zip files into multiple files.')
    parser.add_argument('--split_size_bytes', default=50_000_000, type=int, help='Size of each split file in bytes. Default is 50MB.')
    args = parser.parse_args()

    assert args.encrypt != '' or args.decrypt != '', "Insufficient arguments provided. Please provide a file or folder to encrypt or decrypt."
    assert os.path.isfile(args.key_dir), "Key not found, please generate a key using generate_key(), provide an existing key_dir, or locate the key."
    assert args.split_size_bytes > 0, "split_size_bytes must be greater than 0."
    assert args.split_size_bytes < 100_000_000, "split_size_bytes must be less than 100MB."
    assert os.path.isdir(args.encrypt) or os.path.isfile(args.decrypt), "Please provide an existing file to decrypt or an existing folder to encrypt."
    assert type(args.multifile) == bool, "multifile must be a boolean."
    assert not (args.encrypt != '' and args.decrypt != ''), "Please either encrypt or decrypt, not both."
    
    if args.encrypt != '':
        zip_and_lock_folder(args.encrypt, args.key_dir, args.multifile, args.split_size_bytes)
    else:
        unlock_and_unzip_file(args.decrypt, args.key_dir, args.multifile)
