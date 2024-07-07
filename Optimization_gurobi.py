import pandas as pd
from gurobipy import *
import time
from colors import ColorProfiles as clr

class DHL_Optimization:
    """
    A class to represent the DHL Optimization problem.

    Attributes:
        source_df (pd.DataFrame): DataFrame containing source facility information.
        destination_df (pd.DataFrame): DataFrame containing destination facility information.
        trucking_df (pd.DataFrame): DataFrame containing trucking information.
        model (gurobipy.Model): Gurobi optimization model.
        source_list (list): List of unique source facility IDs.
        destination_list (list): List of unique destination facility IDs.
        routes_list (list): List of possible routes between source and destination facilities.
        consignment_list (list): List of consignment IDs.
        valid_combinations (list): List of valid combinations of routes and consignments.
        trucks (range): Range of truck indices.
        X (dict): Decision variables for consignment assignment to trucks.
        Z (dict): Decision variables for truck usage.
        T (dict): Decision variables for truck release times.
        ArrivalDay (dict): Decision variables for truck arrival days.
        ArrivalTime (dict): Decision variables for truck arrival times.
    """

    def __init__(self):
        """
        Initializes the DHL_Optimization class with default values.
        """
        self.source_df = None
        self.destination_df = None
        self.trucking_df = None
        self.model = Model("DHL_Optimization")
        self.source_list = None
        self.destination_list = None
        self.routes_list = None
        self.consignment_list = None
        self.valid_combinations = None
        self.trucks = range(300)
        self.X = {}
        self.Z = {}
        self.T = {}
        self.ArrivalDay = {}
        self.ArrivalTime = {}
        self.ArrivalDayBinary = {}

    def read_data(self, source_path="Dataset/2024-04-25_OR Praktikum_RWTH Aachen_WBeh_Aufträge.xlsx", 
                destination_path="Dataset/2024-04-25_OR Praktikum_RWTH Aachen_Inputs.xlsx", 
                trucking_path="Dataset/2024-04-25_OSRM_Truck_Distanzen+Fahrtzeiten_PZ_x_PZ.xlsx"):
        """
        Reads data from Excel sheets into pandas DataFrames.

        Args:
            source_path (str): Path to the source facility information Excel file.
            destination_path (str): Path to the destination facility information Excel file.
            trucking_path (str): Path to the trucking information Excel file.
        """
        self.source_df = pd.read_excel(source_path, sheet_name="ta_auftrage_awb_202404251124")
        self.destination_df = pd.read_excel(destination_path, header=1, usecols=lambda x: 'Unnamed' not in x, sheet_name="PZ").dropna(axis='rows')
        self.trucking_df = pd.read_excel(trucking_path, sheet_name="OSRM_Truck")
        self.source_df['geplantes_beladeende'] = pd.to_datetime(self.source_df['geplantes_beladeende'])

    def normalize_shift_times(self):
        """
        Normalizes the shift times in the destination DataFrame to ensure that end times after midnight are handled correctly.
        """
        def normalize(row):
            start_hour = row['Schichtbeginn'].hour + row['Schichtbeginn'].minute / 60
            end_hour = row['Auflegeende (=Sortierschluss/ PZE Sorter Cutoff)'].hour + row['Auflegeende (=Sortierschluss/ PZE Sorter Cutoff)'].minute / 60
            if end_hour < start_hour:
                end_hour += 24  # normalize end_hour for the next day
            return pd.Series([start_hour, end_hour])
        
        self.destination_df[['Schichtbeginn', 'Auflegeende (=Sortierschluss/ PZE Sorter Cutoff)']] = self.destination_df.apply(normalize, axis=1)

    def initialize_model(self):
        """
        Initializes the Gurobi model with decision variables and objective function.
        """
        self.source_list = list(self.trucking_df['Origin_ID'].unique()[:8])  # Index of PZA
        self.destination_list = list(self.trucking_df['Destination_ID'].unique()[:1])  # Index of PZE
        self.routes_list = [(i, j) for i in self.source_list for j in self.destination_list if i != j]
        self.consignment_list = [
            x for (i, j) in self.routes_list
            for x in self.source_df[(self.source_df['quelle_agnr'] == i) & (self.source_df['senke_agnr'] == j)]['id'].values
        ]
        
        self.valid_combinations = [
            (i, j, k) for (i, j) in self.routes_list 
            for k in self.consignment_list if k in self.source_df[(self.source_df['quelle_agnr'] == i) & (self.source_df['senke_agnr'] == j)]['id'].values
        ]

        self.trucks = range(300)

        for (i, j, k, l) in [(i, j, k, l) for (i, j, k) in self.valid_combinations for l in self.trucks]:
            self.X[(i, j, k, l)] = self.model.addVar(vtype=GRB.BINARY, name=f"X_{i}_{j}_{k}_{l}")

        for l in self.trucks:
            self.Z[l] = self.model.addVar(vtype=GRB.BINARY, name=f"Z_{l}")
            self.T[l] = self.model.addVar(lb=0, vtype=GRB.CONTINUOUS, name=f"T_{l}")
            # ArrivalDay[l] = model.addVar(lb = 1, ub=4, vtype=GRB.INTEGER, name=f"ArrivalDay_{l}")
            self.ArrivalTime[l] = self.model.addVar(lb=0, ub=24, vtype=GRB.CONTINUOUS, name=f"ArrivalTime_{i}_{j}_{k}_{l}")
            for d in range(1, 7):  # Maximum number of days to consider
                self.ArrivalDayBinary[(l, d)] = self.model.addVar(vtype=GRB.BINARY, name=f"ArrivalDayBinary_{l}_{d}")
                
        # # Objective function: Minimize the total arrival time
        # self.model.setObjective(
        #     quicksum(self.X[i, j, k, l] for (i, j, k, l) in [(i, j, k, l) for (i, j, k) in self.valid_combinations for l in self.trucks]) 
        #     - quicksum(self.Z[l] for l in self.trucks) 
        #     - quicksum(self.ArrivalDay[l] for l in self.trucks), 
        #     GRB.MAXIMIZE
        # )
        # Objective function: Minimize the total arrival time
        self.model.setObjective(quicksum(self.Z[l]* quicksum(d * self.ArrivalDayBinary[(l, d)] for d in range(1, 7)) for l in self.trucks) ,GRB.MINIMIZE)

    def add_constraints(self):
        """
        Adds constraints to the Gurobi model to ensure the solution is feasible.
        """
        print("Adding constraints to the model...")
        time_saved = time.time()
        
        # Constraint 1: Each truck can carry at most 2 consignments
        for l in self.trucks:
            self.model.addConstr(quicksum(self.X[(i, j, k, l)] for (i, j, k) in self.valid_combinations) <= 2 * self.Z[l])
        
        print(f"1st Constraint Model took {clr.OKYELLOW}{time.time() - time_saved}{clr.ENDC} seconds.")
        time_saved = time.time()
        
        # Constraint 2: Consignment can only be released after the latest release time of the consignments
        for (i, j, k) in self.valid_combinations:
            release_time = self.source_df[self.source_df['id'] == k]['geplantes_beladeende'].dt.hour.values[0]  # Convert to hours
            for l in self.trucks:
                self.model.addConstr(self.T[l] >= release_time * self.X[(i, j, k, l)])
        
        print(f"2nd Constraint Model took {clr.OKYELLOW}{time.time() - time_saved}{clr.ENDC} seconds.")
        time_saved = time.time()
        
        # Constraint 3: Truck must arrive at the destination within the operational hours
        for (i, j, k) in self.valid_combinations:
            start_shift = self.destination_df[self.destination_df['PZA_GNR'] == j]['Schichtbeginn'].values[0]
            end_shift = self.destination_df[self.destination_df['PZA_GNR'] == j]['Auflegeende (=Sortierschluss/ PZE Sorter Cutoff)'].values[0]
            travel_time = self.trucking_df[(self.trucking_df['Origin_ID'] == i) & (self.trucking_df['Destination_ID'] == j)]['OSRM_time [sek]'].values[0] / 3600  # Convert to hours
            for l in self.trucks:
                self.ArrivalTime[(l)] = (self.T[l] + travel_time * self.X[(i, j, k, l)] + 24 * quicksum((d-1)*self.ArrivalDayBinary[(l, d)] for d in range(1, 7))) - 24 * self.model.addVar(vtype=GRB.INTEGER, name=f"multiplier_{i}_{j}_{k}_{l}")
                self.model.addConstr(self.ArrivalTime[(l)] >= start_shift)
                self.model.addConstr(self.ArrivalTime[(l)] <= end_shift)
        
        print(f"3rd Constraint Model took {clr.OKYELLOW}{time.time() - time_saved}{clr.ENDC} seconds.")
        time_saved = time.time()
        
        # 4. Each consignment must be assigned to exactly one truck
        for (i, j, k) in self.valid_combinations:
            self.model.addConstr(quicksum(self.X[(i, j, k, l)] for l in self.trucks if (i, j, k) in self.valid_combinations) == 1)
        
        print(f"4th Constraint Model took {clr.OKYELLOW}{time.time() - time_saved}{clr.ENDC} seconds.")
        time_saved = time.time()
        
        # 5. Sorting Capacity: Each PZE should have enough capacity to accommodate all the incoming trucks                
        for j in self.destination_list:
            working_hours = self.destination_df[self.destination_df['PZA_GNR'] == j]['Auflegeende (=Sortierschluss/ PZE Sorter Cutoff)'].values[0] - self.destination_df[self.destination_df['PZA_GNR'] == j]['Schichtbeginn'].values[0]
            sorting_capacity_per_day =  working_hours/2*self.destination_df[self.destination_df['PZA_GNR'] == j]['Sortierleistung [Sdg je h]'].values[0]
            for d in range(1, 7):  # Maximum number of days to consider
                self.model.addConstr(
                    quicksum(self.X[(i, j, k, l)] * self.source_df[self.source_df['id'] == k]['Sendungsmenge'].values[0] * self.ArrivalDayBinary[(l, d)]
                            for i in self.source_list if j != i
                            for k in self.consignment_list
                            for l in self.trucks
                            if (i, j, k) in self.valid_combinations) <= sorting_capacity_per_day,
                    name=f"SortingCapacity_{j}_{d}"
                )
                    
        for l in self.trucks:
            self.model.addConstr(
                quicksum(self.ArrivalDayBinary[(l, d)] * self.Z[l] for d in range(1, 7)) == 1,
                name = f'Assigning Arrival Day to each used truck'
                )
            
        print(f"5th Constraint Model took {clr.OKYELLOW}{time.time() - time_saved}{clr.ENDC} seconds.")
                
    def solve(self):
        """
        Solves the optimization model and prints the solution.
        """
        print("Solving the optimization problem...")
        start_time = time.time()
        
        self.model.setParam('TimeLimit', 5*60)
        # Solve the model
        callback = MyCallback(self.model)
        self.model.optimize(callback)
        
        if self.model.status in [GRB.OPTIMAL, GRB.TIME_LIMIT, GRB.SUBOPTIMAL]:
            # Extract the data into a DataFrame
            data = []
            k_set = set()
            for (i, j, k, l) in self.X.keys():
                if (self.Z[l].X == 1 ) and (self.X[i,j,k,l].X == 1) :
                    start_shift = self.destination_df[self.destination_df['PZA_GNR'] == j]['Schichtbeginn'].values[0]
                    end_shift = self.destination_df[self.destination_df['PZA_GNR'] == j]['Auflegeende (=Sortierschluss/ PZE Sorter Cutoff)'].values[0]
                    travel_time = self.trucking_df[(self.trucking_df['Origin_ID'] == i) & (self.trucking_df['Destination_ID'] == j)]['OSRM_time [sek]'].values[0] / 3600
                    arrival = quicksum(d*self.ArrivalDayBinary[(l, d)].X for d in range(1, 7))
                    data.append({
                        'Origin(PZA)': i,
                        'Destination (PZE)': j,
                        'Consignment ID': k,
                        'Truck Id': l,
                        'Departure time': self.T[l].X,
                        'Arrival Day': arrival,
                        "Destination Start Shift": start_shift,
                        "Destination End Shift": end_shift,
                        "Travel Time": travel_time
                        
                })
            pd.DataFrame(data).to_csv("output/output.csv")
        else:
            print("No optimal solution found.")
        
        print(f"Optimization completed in {time.time() - start_time} seconds.")

class MyCallback:
    def __init__(self, model, improve_time_limit=5*60):
        self.model = model
        self.improve_time_limit = improve_time_limit
        self.last_improvement_time = time.time()
        self.best_obj_val = float('-inf')

    def __call__(self, model, where):
        if where == GRB.Callback.MIPSOL:
            current_obj_val = model.cbGet(GRB.Callback.MIPSOL_OBJ)
            if current_obj_val > self.best_obj_val:
                self.best_obj_val = current_obj_val
                self.last_improvement_time = time.time()
            elif time.time() - self.last_improvement_time > self.improve_time_limit:
                print(f"No improvement for {self.improve_time_limit / 60} minutes. Stopping optimization.")
                model.terminate()
                
def main():
    """
    The main function to create an instance of DHL_Optimization, read data, normalize shift times,
    initialize the model, add constraints, and solve the optimization problem.
    """
    print(f"{clr.BOLD}*** Welcome to Optimizing Package Center Operations Insight ***{clr.ENDC}")
    optimizer = DHL_Optimization()
    optimizer.read_data()
    optimizer.normalize_shift_times()
    optimizer.initialize_model()
    optimizer.add_constraints()
    optimizer.solve()

if __name__ == "__main__":
    main()