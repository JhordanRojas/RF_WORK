import pandas as pd
import numpy as np

def haversine_distance(lat1, lon1, lat2, lon2):
    """Calculates radial distance in km between two lat/lon coordinates."""
    R = 6371.0
    lat1, lon1, lat2, lon2 = map(np.radians, [lat1, lon1, lat2, lon2])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = np.sin(dlat / 2)**2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2)**2
    return R * (2 * np.arctan2(np.sqrt(a), np.sqrt(1 - a)))

def plan_pci_production(df_live, new_sites_df, collision_km=8.0, confusion_km=12.0, reserved_pci_ranges=[(450, 503)]):
    """
    Production-grade PCI planner with stateful multi-site batching,
    Modulo 6 RS interference scoring, and PCI pool reservation.
    """
    live_db = df_live.copy()
    
    # Pre-build set of reserved PCIs (e.g., Small Cells, DAS)
    reserved_pcis = set()
    for start, end in reserved_pci_ranges:
        reserved_pcis.update(range(start, end + 1))
        
    results = []
    
    for idx, site in new_sites_df.iterrows():
        site_id = site.get('SITE_ID', f'NEW_SITE_{idx+1}')
        lat, lon = site['LATITUD'], site['LONGITUD']
        earfcns = site['EARFCNS'] if isinstance(site['EARFCNS'], list) else [site['EARFCNS']]
        
        # 1. Spatial & Frequency Neighbor Extraction
        live_db['dist'] = haversine_distance(live_db['LATITUD'], live_db['LONGITUD'], lat, lon)
        neighbors = live_db[(live_db['dist'] <= confusion_km) & (live_db['DLEARFCN'].isin(earfcns))].copy()
        
        # 2. Hard Blacklist & Modulo 6 Frequency Penalty Tracking
        blacklist = set(reserved_pcis)
        mod6_penalties = {i: 0 for i in range(6)}
        
        for _, n in neighbors.iterrows():
            pci = int(n['PHYCELLID'])
            dist = n['dist']
            
            # Hard conflict boundaries
            if dist <= confusion_km:
                blacklist.add(pci)
                
            # Track Reference Signal overlap density on close neighbors
            if dist <= collision_km:
                mod6_penalties[pci % 6] += 1
                
        # 3. Evaluate candidate Modulo 3 blocks and score Modulo 6 separation
        best_block = None
        min_penalty = float('inf')
        
        for k in range(0, 504 // 3):
            p0, p1, p2 = 3 * k, 3 * k + 1, 3 * k + 2
            
            # Filter out hard collisions/confusions
            if p0 in blacklist or p1 in blacklist or p2 in blacklist:
                continue
                
            # Score soft RS overlap penalty across all 3 sectors
            penalty = mod6_penalties[p0 % 6] + mod6_penalties[p1 % 6] + mod6_penalties[p2 % 6]
            
            if penalty < min_penalty:
                min_penalty = penalty
                best_block = (p0, p1, p2)
                if penalty == 0:  # Perfectly clear Modulo 6 block
                    break
                    
        # 4. Record Results & Update Live Database Stateually
        if best_block:
            results.append({
                'SITE_ID': site_id,
                'PCI_SEC1': best_block[0],
                'PCI_SEC2': best_block[1],
                'PCI_SEC3': best_block[2],
                'MOD3': f"{best_block[0]%3},{best_block[1]%3},{best_block[2]%3}",
                'MOD6_SCORE_PENALTY': min_penalty,
                'STATUS': 'SUCCESS'
            })
            
            # Inject new site into live_db state for subsequent batch queue items
            for sec_idx, assigned_pci in enumerate(best_block):
                for earfcn in earfcns:
                    new_row = pd.DataFrame([{
                        'ENODOB_NAME': site_id,
                        'LATITUD': lat,
                        'LONGITUD': lon,
                        'DLEARFCN': earfcn,
                        'PHYCELLID': assigned_pci
                    }])
                    live_db = pd.concat([live_db, new_row], ignore_index=True)
        else:
            results.append({
                'SITE_ID': site_id, 'PCI_SEC1': None, 'PCI_SEC2': None, 'PCI_SEC3': None,
                'STATUS': 'FAILED_NO_BLOCK_AVAILABLE'
            })
            
    return pd.DataFrame(results)