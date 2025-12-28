import os
import sys
import re
import shutil
import argparse
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

def process_card_conversion(input_folder: str, output_folder: str, backup_folder: Optional[str] = None, preview_folder: Optional[str] = None, compress_preview: bool = False, reference_folder: Optional[str] = None):
    """
    Scans input_folder, resolves card names (handling sanitization/collisions),
    and generates modded Unity assets.
    """
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)
    
    if backup_folder and not os.path.exists(backup_folder):
        os.makedirs(backup_folder)
        
    if preview_folder and not os.path.exists(preview_folder):
        os.makedirs(preview_folder)

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

        # Reference Logic Setup
        reference_image = None
        if reference_folder:
            if not exists(reference_folder):
                os.makedirs(reference_folder)
            
            # Use sanitized card name for reference file -> Reference/CardName.png
            ref_path = join(reference_folder, f"{card_name}.png")
            if exists(ref_path):
                try:
                    reference_image = Image.open(ref_path).convert("RGBA")
                    print(f"[REFERENCE] Found existing reference: {ref_path}")
                except Exception as e:
                    print(f"[ERROR] Failed to load reference {ref_path}: {e}")

        # Backup Logic (Modified for Reference)
        if backup_folder:
            backup_target_dir = join(backup_folder, "0000", bundle_subfolder)
            if not exists(backup_target_dir):
                os.makedirs(backup_target_dir)
            
            backup_target_path = join(backup_target_dir, bundle_name)
            
            if exists(backup_target_path):
                print(f"[WARNING] Overwriting existing backup: {backup_target_path}")

            try:
                # If we have a reference image, we INJECT it into the backup to ensure it's clean
                if reference_image:
                    env_backup = unity_load(original_bundle_path)
                    for obj in env_backup.objects:
                        if obj.type.name == "Texture2D":
                            data = obj.read()
                            data.m_Width, data.m_Height = reference_image.size
                            data.set_image(
                                img=reference_image,
                                target_format=TextureFormat.RGBA32,
                                mipmap_count=APP_CONFIG.mipmap_count
                            )
                            data.save()
                            break
                    with open(backup_target_path, "wb") as f:
                        f.write(env_backup.file.save(packer=APP_CONFIG.packer))
                    print(f"[BACKUP] Created clean backup from reference: {backup_target_path}")
                else:
                    # Otherwise just copy the game file
                    shutil.copy2(original_bundle_path, backup_target_path)
                    print(f"[BACKUP] Saved original to {backup_target_path}")
            except Exception as e:
                print(f"[ERROR] Failed to backup {bundle_name}: {e}")

        try:
            env = unity_load(original_bundle_path)
            user_image = Image.open(img_path).convert("RGBA")
            modified = False
            original_image_copy = None
            
            # If we already loaded a reference image, use that as the "original" for previews
            if reference_image:
                original_image_copy = reference_image.copy()

            for obj in env.objects:
                if obj.type.name == "Texture2D":
                    data = obj.read()
                    
                    # Capture original image if we haven't already (Snapshot from Game File)
                    if not original_image_copy and (preview_folder or reference_folder):
                        original_image_copy = data.image.copy()
                        
                        # Save this extracted image to Reference folder if specificed and missing
                        if reference_folder:
                             ref_path = join(reference_folder, f"{card_name}.png")
                             if not exists(ref_path):
                                 original_image_copy.save(ref_path)
                                 print(f"[REFERENCE] Saved extracted reference to {ref_path}")
                                 # Update our local reference to this new image for future use
                                 reference_image = original_image_copy

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
                # Save Unity Asset
                target_dir = join(output_folder, "0000", bundle_subfolder)
                if not exists(target_dir):
                    os.makedirs(target_dir)
                
                target_path = join(target_dir, bundle_name)
                
                if exists(target_path):
                    print(f"[WARNING] Overwriting existing asset: {target_path}")

                with open(target_path, "wb") as f:
                    f.write(env.file.save(packer=APP_CONFIG.packer))
                print(f"[SUCCESS] Saved to {target_path}")

                # Save Preview Image
                if preview_folder and original_image_copy:
                    try:
                        # Prepare Preview Directory
                        preview_target_dir = join(preview_folder, "0000", bundle_subfolder)
                        if not exists(preview_target_dir):
                            os.makedirs(preview_target_dir)
                            
                        # Create Side-by-Side (1024x512)
                        # Left: Original, Right: New
                        comparison = Image.new('RGBA', (1024, 512))
                        
                        # Resize images to 512x512 to fit
                        org_resized = original_image_copy.resize((512, 512), Image.Resampling.LANCZOS)
                        new_resized = user_image.resize((512, 512), Image.Resampling.LANCZOS)
                        
                        comparison.paste(org_resized, (0, 0))
                        comparison.paste(new_resized, (512, 0))

                        # Optional Compression (PNG8)
                        if compress_preview:
                            # Quantize to 256 colors (P mode) which saves as 8-bit PNG
                            comparison = comparison.quantize(colors=256, method=2, dither=1)
                        
                        preview_filename = bundle_name + ".png"
                        preview_path = join(preview_target_dir, preview_filename)
                        
                        if exists(preview_path):
                            print(f"[WARNING] Overwriting existing preview: {preview_path}")
                        
                        comparison.save(preview_path)
                        print(f"[PREVIEW] Saved comparison to {preview_path}")

                        # Save Flattened Copy
                        flat_preview_filename = name_without_ext + ".png"
                        flat_preview_path = join(preview_folder, flat_preview_filename)

                        if exists(flat_preview_path):
                            print(f"[WARNING] Overwriting existing flattened preview: {flat_preview_path}")

                        comparison.save(flat_preview_path)
                        print(f"[PREVIEW] Saved flattened copy to {flat_preview_path}")
                        
                    except Exception as pe:
                        print(f"[ERROR] Failed to generate preview for {bundle_name}: {pe}")

            else:
                 print(f"[WARNING] No Texture2D found in bundle {bundle_name}")

        except Exception as e:
            print(f"[ERROR] Failed to process {filename}: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Convert card images to Master Duel Unity assets.")
    parser.add_argument("input_dir", help="Directory containing input images")
    parser.add_argument("-o", "--output", default="modded_assets", help="Directory to save modded assets (default: modded_assets)")
    parser.add_argument("-b", "--backup", help="Directory to save backups of original assets")
    parser.add_argument("-p", "--preview", help="Directory to save side-by-side preview images")
    parser.add_argument("-r", "--reference", help="Directory containing reference (original) card art")
    parser.add_argument("--compress-preview", action="store_true", help="Compress preview images using PNG8 (256 colors)")

    if len(sys.argv) == 1:
        parser.print_help(sys.stderr)
        sys.exit(1)

    args = parser.parse_args()

    if not os.path.exists(args.input_dir):
        print(f"Input directory does not exist: {args.input_dir}")
        sys.exit(1)

    if not APP_CONFIG or not APP_CONFIG.game_path:
        print("Error: Game path not configured in database.")
        sys.exit(1)

    print(f"Starting conversion...")
    print(f"Input: {args.input_dir}")
    print(f"Output: {args.output}")
    if args.backup:
        print(f"Backup: {args.backup}")
    if args.preview:
        print(f"Preview: {args.preview}")
        if args.compress_preview:
            print("Preview Compression: Enabled (PNG8)")
    print(f"Game Data Source: {APP_CONFIG.game_path}")

    process_card_conversion(args.input_dir, args.output, args.backup, args.preview, args.compress_preview, args.reference)
    print("Done.")
