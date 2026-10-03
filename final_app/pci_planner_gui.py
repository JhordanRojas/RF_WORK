import os
import sys
import math
import pandas as pd
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# --- CORE CALCULATIONS ENGINE (PURE PYTHON MATH - NO NUMPY NEEDED) ---

def haversine_distance(lat1, lon1, lat2, lon2):
    """Calculates radial distance in km using Python built-in math module."""
    R = 6371.0
    lat1_r, lon1_r = math.radians(lat1), math.radians(lon1)
    lat2_r, lon2_r = math.radians(lat2), math.radians(lon2)
    dlat = lat2_r - lat1_r
    dlon = lon2_r - lon1_r
    a = math.sin(dlat / 2)**2 + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlon / 2)**2
    return R * (2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)))

def plan_3sector_sites(new_sites_df, df_live, df_guideline, collision_km=8.0, confusion_km=12.0, log_callback=None):
    live_db = df_live.copy()
    
    # Extract reserved PCIs and usable PCGroups
    reserved_pcis = set(df_guideline[df_guideline['Group'].str.contains('Reservado')]['PCI'].unique())
    usable_df = df_guideline[~df_guideline['Group'].str.contains('Reservado')]
    usable_pcgroups = sorted(usable_df['PCGroup'].unique())
    
    results = []
    
    for idx, site in new_sites_df.iterrows():
        site_id = str(site.get('SITE_ID', f'NEW_SITE_{idx+1}'))
        lat = float(site['LATITUD'])
        lon = float(site['LONGITUD'])
        
        if log_callback:
            log_callback(f"Processing {site_id} ({lat}, {lon})...")
            
        # Distance calculation across live DB
        lats = live_db['LATITUD'].values
        lons = live_db['LONGITUD'].values
        live_db['dist'] = [haversine_distance(la, lo, lat, lon) for la, lo in zip(lats, lons)]
        
        nearby_sectors = live_db[live_db['dist'] <= confusion_km]
        
        # Build Blacklist
        blacklist = set(reserved_pcis)
        for pci in nearby_sectors['PHYCELLID'].dropna().astype(int):
            blacklist.add(pci)
            
        # Modulo 6 scoring
        close_neighbors = nearby_sectors[nearby_sectors['dist'] <= collision_km]
        mod6_counts = {i: 0 for i in range(6)}
        for pci in close_neighbors['PHYCELLID'].dropna().astype(int):
            mod6_counts[pci % 6] += 1
            
        best_candidate = None
        min_penalty = float('inf')
        
        for pcg in usable_pcgroups:
            group_rows = df_guideline[df_guideline['PCGroup'] == pcg].sort_values('PCI')
            pcis = group_rows['PCI'].tolist()
            rsis = group_rows['RSI'].tolist()
            grp_name = group_rows['Group'].iloc[0]
            
            p0, p1, p2 = pcis[0], pcis[1], pcis[2]
            
            if p0 in blacklist or p1 in blacklist or p2 in blacklist:
                continue
                
            penalty = mod6_counts[p0 % 6] + mod6_counts[p1 % 6] + mod6_counts[p2 % 6]
            
            if penalty < min_penalty:
                min_penalty = penalty
                best_candidate = {
                    'PCGroup': pcg,
                    'Group_Name': grp_name,
                    'PCIs': (p0, p1, p2),
                    'RSIs': rsis,
                    'Penalty': penalty
                }
                if penalty == 0:
                    break
                    
        if best_candidate:
            p0, p1, p2 = best_candidate['PCIs']
            r0, r1, r2 = best_candidate['RSIs']
            
            res_dict = {
                'SITE_ID': site_id,
                'LATITUDE': lat,
                'LONGITUDE': lon,
                'PCGroup': best_candidate['PCGroup'],
                'Group_Name': best_candidate['Group_Name'],
                'Sec1_PCI': p0, 'Sec1_RSI': r0,
                'Sec2_PCI': p1, 'Sec2_RSI': r1,
                'Sec3_PCI': p2, 'Sec3_RSI': r2,
                'MOD3': f"{p0%3},{p1%3},{p2%3}",
                'RS_Penalty': best_candidate['Penalty'],
                'STATUS': 'SUCCESS'
            }
            results.append(res_dict)
            
            # Stateful update for multi-site batch runs
            for assigned_pci in (p0, p1, p2):
                new_row = pd.DataFrame([{
                    'ENODOB_NAME': site_id,
                    'LATITUD': lat,
                    'LONGITUD': lon,
                    'PHYCELLID': assigned_pci
                }])
                live_db = pd.concat([live_db, new_row], ignore_index=True)
        else:
            results.append({
                'SITE_ID': site_id, 'LATITUDE': lat, 'LONGITUDE': lon,
                'PCGroup': '-', 'Group_Name': '-',
                'Sec1_PCI': '-', 'Sec1_RSI': '-',
                'Sec2_PCI': '-', 'Sec2_RSI': '-',
                'Sec3_PCI': '-', 'Sec3_RSI': '-',
                'MOD3': '-', 'RS_Penalty': '-',
                'STATUS': 'NO_CLEAN_GROUP'
            })
            
    return pd.DataFrame(results)

# --- TKINTER INTERFACE ---

class LTEPCIPlannerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("LTE 3-Sector PCI & PRACH Planner")
        self.root.geometry("1100x750")
        self.root.minsize(900, 600)
        
        self.df_results = None
        self.build_ui()
        
    def build_ui(self):
        style = ttk.Style()
        style.theme_use('clam')
        
        header = tk.Label(self.root, text="LTE Portable PCI & PRACH Allocation Tool", 
                          font=("Segoe UI", 16, "bold"), bg="#1e293b", fg="white", py=12)
        header.pack(fill=tk.X)
        
        main_frame = ttk.Frame(self.root, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # 1. Inputs Frame
        files_frame = ttk.LabelFrame(main_frame, text=" 1. Reference Data Files ", padding=10)
        files_frame.pack(fill=tk.X, pady=5)
        
        ttk.Label(files_frame, text="Live Provisioning File (CSV):").grid(row=0, column=0, sticky=tk.W, pady=3)
        self.entry_live = ttk.Entry(files_frame, width=65)
        self.entry_live.grid(row=0, column=1, padx=5, pady=3)
        ttk.Button(files_frame, text="Browse...", command=self.browse_live).grid(row=0, column=2, padx=3)
        
        ttk.Label(files_frame, text="Guideline File (Excel):").grid(row=1, column=0, sticky=tk.W, pady=3)
        self.entry_guide = ttk.Entry(files_frame, width=65)
        self.entry_guide.grid(row=1, column=1, padx=5, pady=3)
        ttk.Button(files_frame, text="Browse...", command=self.browse_guide).grid(row=1, column=2, padx=3)
        
        # 2. Target Sites & Parameters Frame
        config_frame = ttk.LabelFrame(main_frame, text=" 2. Planning Configuration & Target Sites ", padding=10)
        config_frame.pack(fill=tk.X, pady=5)
        
        self.tab_control = ttk.Notebook(config_frame)
        self.tab_single = ttk.Frame(self.tab_control, padding=10)
        self.tab_batch = ttk.Frame(self.tab_control, padding=10)
        
        self.tab_control.add(self.tab_single, text=" Single Site Planning ")
        self.tab_control.add(self.tab_batch, text=" Batch Planning (CSV/Excel) ")
        self.tab_control.pack(fill=tk.X, expand=True, pady=5)
        
        # Single Site Inputs
        ttk.Label(self.tab_single, text="Site ID:").grid(row=0, column=0, padx=5, pady=3)
        self.entry_site_id = ttk.Entry(self.tab_single, width=18)
        self.entry_site_id.grid(row=0, column=1, padx=5, pady=3)
        self.entry_site_id.insert(0, "NEW_SITE_01")
        
        ttk.Label(self.tab_single, text="Latitude:").grid(row=0, column=2, padx=5, pady=3)
        self.entry_lat = ttk.Entry(self.tab_single, width=15)
        self.entry_lat.grid(row=0, column=3, padx=5, pady=3)
        self.entry_lat.insert(0, "-5.99304")
        
        ttk.Label(self.tab_single, text="Longitude:").grid(row=0, column=4, padx=5, pady=3)
        self.entry_lon = ttk.Entry(self.tab_single, width=15)
        self.entry_lon.grid(row=0, column=5, padx=5, pady=3)
        self.entry_lon.insert(0, "-79.74397")
        
        # Batch Inputs
        ttk.Label(self.tab_batch, text="Target Sites File:").grid(row=0, column=0, padx=5, pady=3)
        self.entry_batch_file = ttk.Entry(self.tab_batch, width=50)
        self.entry_batch_file.grid(row=0, column=1, padx=5, pady=3)
        ttk.Button(self.tab_batch, text="Browse...", command=self.browse_batch).grid(row=0, column=2, padx=3)
        
        # Parameters
        param_subframe = ttk.Frame(config_frame)
        param_subframe.pack(fill=tk.X, pady=5)
        
        ttk.Label(param_subframe, text="Collision Dist (km):").pack(side=tk.LEFT, padx=(5, 2))
        self.entry_collision = ttk.Entry(param_subframe, width=8)
        self.entry_collision.pack(side=tk.LEFT, padx=(0, 15))
        self.entry_collision.insert(0, "8.0")
        
        ttk.Label(param_subframe, text="Confusion Dist (km):").pack(side=tk.LEFT, padx=(5, 2))
        self.entry_confusion = ttk.Entry(param_subframe, width=8)
        self.entry_confusion.pack(side=tk.LEFT, padx=(0, 20))
        self.entry_confusion.insert(0, "12.0")
        
        btn_run = tk.Button(param_subframe, text="⚡ RUN PCI ALLOCATION", font=("Segoe UI", 10, "bold"),
                            bg="#16a34a", fg="white", activebackground="#15803d", activeforeground="white",
                            padx=15, pady=3, command=self.run_allocation)
        btn_run.pack(side=tk.RIGHT, padx=5)

        # 3. Output Table Frame
        out_frame = ttk.LabelFrame(main_frame, text=" 3. Allocation Results ", padding=10)
        out_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        
        columns = ("SITE_ID", "PCGroup", "Group_Name", "Sec1_PCI", "Sec1_RSI", 
                   "Sec2_PCI", "Sec2_RSI", "Sec3_PCI", "Sec3_RSI", "MOD3", "STATUS")
        
        self.tree = ttk.Treeview(out_frame, columns=columns, show='headings', height=10)
        for col in columns:
            self.tree.heading(col, text=col)
            self.tree.column(col, width=90, anchor=tk.CENTER)
            
        self.tree.column("SITE_ID", width=120)
        self.tree.column("Group_Name", width=110)
        
        vsb = ttk.Scrollbar(out_frame, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(out_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        
        self.tree.grid(row=0, column=0, sticky='nsew')
        vsb.grid(row=0, column=1, sticky='ns')
        hsb.grid(row=1, column=0, sticky='ew')
        out_frame.grid_rowconfigure(0, weight=1)
        out_frame.grid_columnconfigure(0, weight=1)
        
        # Export Bar
        export_bar = ttk.Frame(main_frame)
        export_bar.pack(fill=tk.X, pady=5)
        
        self.lbl_status = ttk.Label(export_bar, text="Ready.", font=("Segoe UI", 9, "italic"))
        self.lbl_status.pack(side=tk.LEFT)
        
        btn_export = tk.Button(export_bar, text="💾 Export Results to Excel/CSV", font=("Segoe UI", 9, "bold"),
                               bg="#2563eb", fg="white", padx=10, command=self.export_results)
        btn_export.pack(side=tk.RIGHT)

    def browse_live(self):
        f = filedialog.askopenfilename(filetypes=[("CSV Files", "*.csv"), ("All Files", "*.*")])
        if f: self.entry_live.delete(0, tk.END); self.entry_live.insert(0, f)
        
    def browse_guide(self):
        f = filedialog.askopenfilename(filetypes=[("Excel Files", "*.xlsx *.xls"), ("All Files", "*.*")])
        if f: self.entry_guide.delete(0, tk.END); self.entry_guide.insert(0, f)
        
    def browse_batch(self):
        f = filedialog.askopenfilename(filetypes=[("CSV/Excel Files", "*.csv *.xlsx *.xls"), ("All Files", "*.*")])
        if f: self.entry_batch_file.delete(0, tk.END); self.entry_batch_file.insert(0, f)

    def log(self, msg):
        self.lbl_status.config(text=msg)
        self.root.update_idletasks()

    def run_allocation(self):
        live_path = self.entry_live.get().strip()
        guide_path = self.entry_guide.get().strip()
        
        if not os.path.exists(live_path) or not os.path.exists(guide_path):
            messagebox.showerror("File Error", "Please specify valid paths for both Live Data CSV and Guideline Excel.")
            return
            
        try:
            collision_km = float(self.entry_collision.get())
            confusion_km = float(self.entry_confusion.get())
        except ValueError:
            messagebox.showerror("Input Error", "Collision and Confusion distances must be numeric.")
            return

        try:
            self.log("Loading reference data files...")
            df_live = pd.read_csv(live_path, low_memory=False)
            df_guideline = pd.read_excel(guide_path, sheet_name='INFO DF')
        except Exception as e:
            messagebox.showerror("Data Load Error", f"Failed to load reference files:\n{str(e)}")
            return

        active_tab = self.tab_control.index(self.tab_control.select())
        if active_tab == 0:
            site_id = self.entry_site_id.get().strip()
            try:
                lat = float(self.entry_lat.get())
                lon = float(self.entry_lon.get())
            except ValueError:
                messagebox.showerror("Input Error", "Latitude and Longitude must be valid numbers.")
                return
            sites_df = pd.DataFrame([{'SITE_ID': site_id, 'LATITUD': lat, 'LONGITUD': lon}])
        else:
            batch_path = self.entry_batch_file.get().strip()
            if not os.path.exists(batch_path):
                messagebox.showerror("File Error", "Please select a valid Batch file.")
                return
            if batch_path.endswith('.csv'):
                sites_df = pd.read_csv(batch_path)
            else:
                sites_df = pd.read_excel(batch_path)
                
            required_cols = {'LATITUD', 'LONGITUD'}
            if not required_cols.issubset(set(sites_df.columns)):
                messagebox.showerror("Format Error", "Batch file must contain 'LATITUD' and 'LONGITUD' columns.")
                return

        self.log("Executing PCI & PRACH assignment...")
        self.df_results = plan_3sector_sites(sites_df, df_live, df_guideline, collision_km, confusion_km, log_callback=self.log)
        
        for row in self.tree.get_children():
            self.tree.delete(row)
            
        for _, r in self.df_results.iterrows():
            self.tree.insert("", tk.END, values=(
                r['SITE_ID'], r['PCGroup'], r['Group_Name'],
                r['Sec1_PCI'], r['Sec1_RSI'],
                r['Sec2_PCI'], r['Sec2_RSI'],
                r['Sec3_PCI'], r['Sec3_RSI'],
                r['MOD3'], r['STATUS']
            ))
            
        self.log(f"Done! Processed {len(self.df_results)} site(s).")
        messagebox.showinfo("Success", f"PCI Allocation complete for {len(self.df_results)} site(s).")

    def export_results(self):
        if self.df_results is None or self.df_results.empty:
            messagebox.showwarning("Export Warning", "No results to export. Run allocation first.")
            return
            
        save_path = filedialog.asksaveasfilename(
            defaultextension=".xlsx",
            filetypes=[("Excel Spreadsheet", "*.xlsx"), ("CSV File", "*.csv")]
        )
        if save_path:
            try:
                if save_path.endswith('.csv'):
                    self.df_results.to_csv(save_path, index=False)
                else:
                    self.df_results.to_excel(save_path, index=False)
                messagebox.showinfo("Export Success", f"Results exported to:\n{save_path}")
            except Exception as e:
                messagebox.showerror("Export Error", f"Could not save file:\n{str(e)}")

if __name__ == '__main__':
    root = tk.Tk()
    app = LTEPCIPlannerApp(root)
    root.mainloop()