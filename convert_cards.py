import os
import sys
import re
from glob import glob
from os.path import basename, join, exists
from typing import Optional, Dict, List

# Ensure we can import from the current directory
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.append(current_dir)

from PIL import Image
from UnityPy import load as unity_load
from UnityPy.enums import TextureFormat

# Import local modules
from database.models import CardModel
from database.objects import session
from util.constants import APP_CONFIG

# --- Helpers ---

def sanitize_filename(name: str) -> str:
    """
    Sanitizes a card name to match common Windows filename conventions.
    Replaces illegal characters ( < > : " / \ | ? * ) with underscores.
    """
    return re.sub(r'[<>:"/\\|?*]', '_', name)

def load_card_database() -> Dict[str, List[CardModel]]:
    """
    Loads all cards from the database and indexes them by their sanitized filename.
    Returns a dict mapping { 'sanitized_name_lower': [CardModel, ...] }
    """
    print("Loading card database...")
    all_cards = session.query(CardModel).all()
    card_map = {}
    
    for card in all_cards:
        # Sanitize the DB name to what it would be as a file
        safe_name = sanitize_filename(card.name)
        key = safe_name.lower()
        
        if key not in card_map:
            card_map[key] = []
        card_map[key].append(card)
        
    return card_map

def resolve_card(filename_no_ext: str, card_map: Dict[str, List[CardModel]]) -> Optional[CardModel]:
    """
    Resolves a filename to a single CardModel.
    Prompts user if collision occurs.
    """
    key = filename_no_ext.lower()
    matches = card_map.get(key)
    
    if not matches:
        return None

    if len(matches) == 1:
        return matches[0]

    # Collision Handling
    print(f"\n[COLLISION] The file '{filename_no_ext}' matches multiple cards:")
    for i, card in enumerate(matches):
        print(f"  {i+1}: {card.name} (Bundle: {card.bundle})")
    
    while True:
        try:
            choice = input(f"Select card (1-{len(matches)}) or 's' to skip: ").strip().lower()
            if choice == 's':
                return None
            idx = int(choice) - 1
            if 0 <= idx < len(matches):
                return matches[idx]
        except ValueError:
            pass
        print("Invalid selection.")

# --- Main Logic ---

def process_card_conversion(input_folder: str, output_folder: str):
    """
    Scans input_folder, resolves card names (handling sanitization/collisions),
    and generates modded Unity assets.
    """
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)

    # 1. Load Database
    card_map = load_card_database()

    # 2. Scan Files
    image_extensions = ['*.png', '*.jpg', '*.jpeg']
    image_files = []
    for ext in image_extensions:
        image_files.extend(glob(join(input_folder, ext)))
    
    print(f"Found {len(image_files)} images in {input_folder}")

    for img_path in image_files:
        filename = basename(img_path)
        name_without_ext = os.path.splitext(filename)[0]

        # 3. Resolve
        card = resolve_card(name_without_ext, card_map)
        
        if not card:
            print(f"[SKIP] Could not resolve card for file: {filename}")
            continue
            
        bundle_name = card.bundle
        card_name = card.name
        
        print(f"[PROCESSING] '{card_name}' -> Bundle: {bundle_name}")

        # 4. Processing
        bundle_subfolder = bundle_name[:2]
        original_bundle_path = join(APP_CONFIG.game_path, "0000", bundle_subfolder, bundle_name)

        if not exists(original_bundle_path):
            print(f"[ERROR] Bundle file not found in game data: {original_bundle_path}")
            continue

        try:
            env = unity_load(original_bundle_path)
            user_image = Image.open(img_path).convert("RGBA")
            modified = False
            
            for obj in env.objects:
                if obj.type.name == "Texture2D":
                    data = obj.read()
                    
                    data.m_Width, data.m_Height = user_image.size
                    data.set_image(
                        img=user_image,
                        target_format=TextureFormat.RGBA32,
                        mipmap_count=APP_CONFIG.mipmap_count
                    )
                    data.save()
                    modified = True
                    break 
            
            if modified:
                target_dir = join(output_folder, "0000", bundle_subfolder)
                if not exists(target_dir):
                    os.makedirs(target_dir)
                
                target_path = join(target_dir, bundle_name)
                
                with open(target_path, "wb") as f:
                    f.write(env.file.save(packer=APP_CONFIG.packer))
                print(f"[SUCCESS] Saved to {target_path}")
            else:
                 print(f"[WARNING] No Texture2D found in bundle {bundle_name}")

        except Exception as e:
            print(f"[ERROR] Failed to process {filename}: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python convert_cards.py <input_folder_path> [output_folder_path]")
        sys.exit(1)

    input_dir = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else "modded_assets"

    if not os.path.exists(input_dir):
        print(f"Input directory does not exist: {input_dir}")
        sys.exit(1)

    if not APP_CONFIG or not APP_CONFIG.game_path:
        print("Error: Game path not configured in database.")
        sys.exit(1)

    process_card_conversion(input_dir, output_dir)
    print("Done.")
