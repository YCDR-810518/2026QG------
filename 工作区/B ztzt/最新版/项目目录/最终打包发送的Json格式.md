# Json

````json
{
  "engine_snapshot": [
    {
      "tick": 120,
      "timestamp": "2026-08-06 08:02:00",
      "node_id": "north_gate",
      "people": 152,
      "vehicles": 6,
      "density": 0.71,
      "level": "HIGH",
      "gate_status": "restricted",
      "gate_flow_rate": 120,
      "door_status": "",
      "door_flow_rate": "",
      "signal_status": "green",
      "signal_flow_rate": 180
    },
    { "tick": 120, "timestamp": "2026-08-06 08:02:00", "node_id": "canteen", "people": 214, "vehicles": 2, "density": 0.64, "level": "MEDIUM", "gate_status": "", "gate_flow_rate": "", "door_status": "open", "door_flow_rate": "", "signal_status": "", "signal_flow_rate": "" }
  ],
  "vehicle_paths": [
    { "src": "south_gate", "dst": "admin_bld", "path": ["south_gate", "road_3", "roundabout", "admin_bld"], "travelTime": 12.5 },
    { "src": "west_gate", "dst": "plant_2", "path": ["west_gate", "road_7", "plant_2"], "travelTime": 8.3 }
  ],
  "predict_network": [
    { "node_id": "north_gate", "density_pred": 0.78, "level": "HIGH", "heat": 0.92 }
  ],
  "predict_hotspots": [
    { "region": "north_gate_zone", "nodes": ["north_gate", "gate_plaza"], "risk_level": "HIGH", "density": 0.75 }
  ],
  "prediction": {
    "period": { "start": "2026-08-06 08:00:00", "end": "2026-08-06 09:00:00" },
    "density_stats": { "north_gate": 0.81, "canteen": 0.66 }
  },
  "alerts": [
    { "level": "HIGH", "type": "over_density", "node_id": "north_gate", "current_density": 0.81, "suggested_action": "gate_restrict" }
  ],
  "cav_stats": {
    "avg_speed_kmh": 18.42,
    "low_speed_ratio": 0.0833,
    "n_vehicles": 36,
    "n_low_speed": 3
  },
  "micro_validation_results": {
    "meta": {
      "scope": "仅统计 src_node ∈ gate_nodes 的大门入园车辆",
      "per_node_aggregation": "按 dst_node 归集（近似）",
      "delay_definition": "信号排队 + 低速行驶(<5km/h)，不含计划性出发等待",
      "n_people": 2000, "n_vehicles": 300, "n_ticks": 7200, "seed": 42
    },
    "per_node": {
      "admin_bld": {
        "avg_speed_idm": 14.8, "avg_speed_cav": 21.6,
        "efficiency_gain_pct": 0.459,
        "avg_delay_time": 18.4,
        "throughput": 45, "n_trips": 45,
        "avg_travel_time_idm": 482.0, "avg_travel_time_cav": 356.0
      },
      "plant_2": {
        "avg_speed_idm": 13.2, "avg_speed_cav": 19.8,
        "efficiency_gain_pct": 0.5,
        "avg_delay_time": 22.1,
        "throughput": 38, "n_trips": 38,
        "avg_travel_time_idm": 510.0, "avg_travel_time_cav": 372.0
      }
    },
    "od_stats": {
      "south_gate|admin_bld": { "trips": 26, "avg_travel_time_idm": 470.0, "avg_travel_time_cav": 350.0, "avg_speed_idm": 15.1, "avg_speed_cav": 22.0 },
      "west_gate|plant_2": { "trips": 19, "avg_travel_time_idm": 520.0, "avg_travel_time_cav": 380.0, "avg_speed_idm": 13.0, "avg_speed_cav": 20.5 }
    }
  }
}
````

