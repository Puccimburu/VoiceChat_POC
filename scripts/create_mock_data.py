"""
Creates mock data for FOUR demo customers in MongoDB to demonstrate multi-tenancy:
  - Customer 1: "Grand Clubhouse"       (database: Test)
  - Customer 2: "AquaFit Swimming Club" (database: AquaFitDB)
  - Customer 3: "Riverside Tennis Club" (database: TennisDB)
  - Customer 4: "City Fitness Center"   (database: FitnessDB)

Also registers all customers in the platform (api_keys collection in Test DB).
All member/staff names are English.
"""
import secrets
from datetime import datetime, timedelta
from pymongo import MongoClient

PLATFORM_MONGO_URI = "mongodb://localhost:27017/"
PLATFORM_DB = "Test"

client = MongoClient(PLATFORM_MONGO_URI)


# ==============================================================
# CUSTOMER 1 — Grand Clubhouse  (uses Test database)
# ==============================================================
def create_clubhouse_data():
    db = client[PLATFORM_DB]

    for col in ["facilities", "members", "classes", "bookings", "staff"]:
        db[col].drop()
    print("  Cleared existing clubhouse collections")

    # Facilities
    db.facilities.insert_many([
        {
            "facility_id": "GYM001", "name": "Main Gym", "type": "gym",
            "capacity": 50,
            "operating_hours": {"weekday": "06:00-22:00", "weekend": "07:00-20:00"},
            "amenities": ["Cardio Equipment", "Free Weights", "Machines", "Personal Training"],
            "rate_per_hour": 15, "available": True
        },
        {
            "facility_id": "POOL001", "name": "Olympic Swimming Pool", "type": "swimming_pool",
            "capacity": 30,
            "operating_hours": {"weekday": "06:00-21:00", "weekend": "07:00-19:00"},
            "amenities": ["8 Lanes", "Heated Water", "Kids Pool", "Changing Rooms"],
            "rate_per_hour": 20, "available": True
        },
        {
            "facility_id": "TENNIS001", "name": "Tennis Court 1", "type": "tennis_court",
            "capacity": 4,
            "operating_hours": {"weekday": "06:00-22:00", "weekend": "06:00-22:00"},
            "amenities": ["Flood Lights", "Court Equipment", "Seating"],
            "rate_per_hour": 18, "available": True
        },
        {
            "facility_id": "TENNIS002", "name": "Tennis Court 2", "type": "tennis_court",
            "capacity": 4,
            "operating_hours": {"weekday": "06:00-22:00", "weekend": "06:00-22:00"},
            "amenities": ["Flood Lights", "Court Equipment"],
            "rate_per_hour": 18, "available": True
        },
        {
            "facility_id": "SQUASH001", "name": "Squash Court", "type": "squash_court",
            "capacity": 2,
            "operating_hours": {"weekday": "07:00-21:00", "weekend": "08:00-20:00"},
            "amenities": ["Professional Court", "Equipment Rental"],
            "rate_per_hour": 12, "available": True
        },
        {
            "facility_id": "YOGA001", "name": "Yoga Studio", "type": "yoga_studio",
            "capacity": 20,
            "operating_hours": {"weekday": "06:00-20:00", "weekend": "07:00-18:00"},
            "amenities": ["Mats", "Blocks", "Straps", "Meditation Corner"],
            "rate_per_hour": 10, "available": True
        },
        {
            "facility_id": "SPIN001", "name": "Spin Studio", "type": "spin_studio",
            "capacity": 25,
            "operating_hours": {"weekday": "06:00-21:00", "weekend": "07:00-19:00"},
            "amenities": ["25 Bikes", "Sound System", "Fan Cooling"],
            "rate_per_hour": 12, "available": True
        },
    ])
    print(f"  Created {db.facilities.count_documents({})} facilities")

    # Members (10, all English names)
    db.members.insert_many([
        {
            "member_id": "MEM001", "name": "James Mitchell", "email": "james.mitchell@email.com",
            "phone": "+44-7911-123001", "membership_type": "Gold",
            "membership_expiry": (datetime.now() + timedelta(days=365)).isoformat(),
            "joined_date": "2023-01-15", "emergency_contact": "+44-7911-123002"
        },
        {
            "member_id": "MEM002", "name": "Sarah Thompson", "email": "sarah.thompson@email.com",
            "phone": "+44-7911-123010", "membership_type": "Silver",
            "membership_expiry": (datetime.now() + timedelta(days=180)).isoformat(),
            "joined_date": "2023-06-20", "emergency_contact": "+44-7911-123011"
        },
        {
            "member_id": "MEM003", "name": "Oliver Bennett", "email": "oliver.bennett@email.com",
            "phone": "+44-7911-123020", "membership_type": "Platinum",
            "membership_expiry": (datetime.now() + timedelta(days=730)).isoformat(),
            "joined_date": "2022-03-10", "emergency_contact": "+44-7911-123021"
        },
        {
            "member_id": "MEM004", "name": "Emma Clarke", "email": "emma.clarke@email.com",
            "phone": "+44-7911-123030", "membership_type": "Silver",
            "membership_expiry": (datetime.now() + timedelta(days=90)).isoformat(),
            "joined_date": "2024-01-05", "emergency_contact": "+44-7911-123031"
        },
        {
            "member_id": "MEM005", "name": "William Davies", "email": "william.davies@email.com",
            "phone": "+44-7911-123040", "membership_type": "Gold",
            "membership_expiry": (datetime.now() + timedelta(days=300)).isoformat(),
            "joined_date": "2023-09-01", "emergency_contact": "+44-7911-123041"
        },
        {
            "member_id": "MEM006", "name": "Charlotte Wilson", "email": "charlotte.wilson@email.com",
            "phone": "+44-7911-123050", "membership_type": "Platinum",
            "membership_expiry": (datetime.now() + timedelta(days=500)).isoformat(),
            "joined_date": "2022-11-20", "emergency_contact": "+44-7911-123051"
        },
        {
            "member_id": "MEM007", "name": "Henry Anderson", "email": "henry.anderson@email.com",
            "phone": "+44-7911-123060", "membership_type": "Bronze",
            "membership_expiry": (datetime.now() + timedelta(days=60)).isoformat(),
            "joined_date": "2024-08-15", "emergency_contact": "+44-7911-123061"
        },
        {
            "member_id": "MEM008", "name": "Sophie Taylor", "email": "sophie.taylor@email.com",
            "phone": "+44-7911-123070", "membership_type": "Gold",
            "membership_expiry": (datetime.now() + timedelta(days=400)).isoformat(),
            "joined_date": "2023-04-22", "emergency_contact": "+44-7911-123071"
        },
        {
            "member_id": "MEM009", "name": "George Brown", "email": "george.brown@email.com",
            "phone": "+44-7911-123080", "membership_type": "Silver",
            "membership_expiry": (datetime.now() + timedelta(days=200)).isoformat(),
            "joined_date": "2023-12-01", "emergency_contact": "+44-7911-123081"
        },
        {
            "member_id": "MEM010", "name": "Lucy White", "email": "lucy.white@email.com",
            "phone": "+44-7911-123090", "membership_type": "Bronze",
            "membership_expiry": (datetime.now() + timedelta(days=45)).isoformat(),
            "joined_date": "2025-01-10", "emergency_contact": "+44-7911-123091"
        },
    ])
    print(f"  Created {db.members.count_documents({})} members")

    # Classes
    db.classes.insert_many([
        {
            "class_id": "CLS001", "name": "Morning Yoga", "instructor": "Helen Carter",
            "facility_id": "YOGA001",
            "schedule": {"days": ["Monday", "Wednesday", "Friday"], "time": "07:00-08:00"},
            "capacity": 20, "enrolled": 15, "fees": 60, "duration_weeks": 8
        },
        {
            "class_id": "CLS002", "name": "Swimming for Beginners", "instructor": "David Morris",
            "facility_id": "POOL001",
            "schedule": {"days": ["Tuesday", "Thursday", "Saturday"], "time": "08:00-09:00"},
            "capacity": 12, "enrolled": 10, "fees": 80, "duration_weeks": 6
        },
        {
            "class_id": "CLS003", "name": "Advanced Swimming", "instructor": "David Morris",
            "facility_id": "POOL001",
            "schedule": {"days": ["Monday", "Wednesday", "Friday"], "time": "18:00-19:00"},
            "capacity": 10, "enrolled": 8, "fees": 90, "duration_weeks": 6
        },
        {
            "class_id": "CLS004", "name": "HIIT Training", "instructor": "Robert Harris",
            "facility_id": "GYM001",
            "schedule": {"days": ["Tuesday", "Thursday"], "time": "06:30-07:30"},
            "capacity": 15, "enrolled": 12, "fees": 70, "duration_weeks": 4
        },
        {
            "class_id": "CLS005", "name": "Power Yoga", "instructor": "Helen Carter",
            "facility_id": "YOGA001",
            "schedule": {"days": ["Saturday", "Sunday"], "time": "08:00-09:30"},
            "capacity": 18, "enrolled": 14, "fees": 55, "duration_weeks": 8
        },
        {
            "class_id": "CLS006", "name": "Spin Cycling", "instructor": "Robert Harris",
            "facility_id": "SPIN001",
            "schedule": {"days": ["Monday", "Wednesday", "Friday"], "time": "06:00-07:00"},
            "capacity": 25, "enrolled": 20, "fees": 65, "duration_weeks": 4
        },
        {
            "class_id": "CLS007", "name": "Aqua Aerobics", "instructor": "Susan Robinson",
            "facility_id": "POOL001",
            "schedule": {"days": ["Tuesday", "Thursday"], "time": "10:00-11:00"},
            "capacity": 15, "enrolled": 11, "fees": 50, "duration_weeks": 6
        },
    ])
    print(f"  Created {db.classes.count_documents({})} classes")

    # Bookings
    today = datetime.now().strftime("%Y-%m-%d")
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    day_after = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d")
    bookings = []
    booking_data = [
        ("BK001", "MEM001", "James Mitchell",   "TENNIS001", "Tennis Court 1",        today,     "18:00-19:00", 18),
        ("BK002", "MEM003", "Oliver Bennett",   "POOL001",   "Olympic Swimming Pool",  today,     "07:00-08:00", 20),
        ("BK003", "MEM005", "William Davies",   "GYM001",    "Main Gym",               today,     "12:00-13:00", 15),
        ("BK004", "MEM008", "Sophie Taylor",    "YOGA001",   "Yoga Studio",            today,     "09:00-10:00", 10),
        ("BK101", "MEM001", "James Mitchell",   "TENNIS001", "Tennis Court 1",         tomorrow,  "18:00-19:00", 18),
        ("BK102", "MEM002", "Sarah Thompson",   "YOGA001",   "Yoga Studio",            tomorrow,  "07:00-08:00", 10),
        ("BK103", "MEM004", "Emma Clarke",      "GYM001",    "Main Gym",               tomorrow,  "09:00-10:00", 15),
        ("BK104", "MEM003", "Oliver Bennett",   "SQUASH001", "Squash Court",           day_after, "10:00-11:00", 12),
        ("BK105", "MEM006", "Charlotte Wilson", "POOL001",   "Olympic Swimming Pool",  day_after, "07:00-08:00", 20),
        ("BK106", "MEM007", "Henry Anderson",   "SPIN001",   "Spin Studio",            day_after, "06:00-07:00", 12),
        ("BK107", "MEM009", "George Brown",     "TENNIS002", "Tennis Court 2",         day_after, "17:00-18:00", 18),
        ("BK108", "MEM010", "Lucy White",       "GYM001",    "Main Gym",               tomorrow,  "17:00-18:00", 15),
    ]
    for b in booking_data:
        bookings.append({
            "booking_id": b[0], "member_id": b[1], "member_name": b[2],
            "facility_id": b[3], "facility_name": b[4], "booking_date": b[5],
            "time_slot": b[6], "status": "confirmed",
            "created_at": datetime.now().isoformat(), "amount": b[7]
        })
    db.bookings.insert_many(bookings)
    print(f"  Created {db.bookings.count_documents({})} bookings")

    # Staff
    db.staff.insert_many([
        {
            "staff_id": "STF001", "name": "Helen Carter", "role": "Yoga Instructor",
            "email": "helen.carter@grandclubhouse.com", "phone": "+44-7911-200001",
            "specialization": ["Hatha Yoga", "Power Yoga", "Meditation"],
            "experience_years": 8, "available_slots": ["Morning", "Evening"]
        },
        {
            "staff_id": "STF002", "name": "David Morris", "role": "Swimming Coach",
            "email": "david.morris@grandclubhouse.com", "phone": "+44-7911-200002",
            "specialization": ["Freestyle", "Butterfly", "Kids Swimming"],
            "experience_years": 12, "available_slots": ["Morning", "Evening"]
        },
        {
            "staff_id": "STF003", "name": "Robert Harris", "role": "Fitness Trainer",
            "email": "robert.harris@grandclubhouse.com", "phone": "+44-7911-200003",
            "specialization": ["HIIT", "Weight Training", "Cardio", "Spin"],
            "experience_years": 6, "available_slots": ["Morning", "Afternoon", "Evening"]
        },
        {
            "staff_id": "STF004", "name": "Susan Robinson", "role": "Aqua Aerobics Instructor",
            "email": "susan.robinson@grandclubhouse.com", "phone": "+44-7911-200004",
            "specialization": ["Aqua Aerobics", "Water Fitness", "Senior Fitness"],
            "experience_years": 5, "available_slots": ["Morning", "Afternoon"]
        },
        {
            "staff_id": "STF005", "name": "Michael Turner", "role": "Front Desk Manager",
            "email": "michael.turner@grandclubhouse.com", "phone": "+44-7911-200005",
            "specialization": ["Member Services", "Scheduling", "Reception"],
            "experience_years": 4, "available_slots": ["Morning", "Afternoon"]
        },
    ])
    print(f"  Created {db.staff.count_documents({})} staff members")

    return {
        "connection_string": PLATFORM_MONGO_URI,
        "database": PLATFORM_DB,
        "collections": ["facilities", "members", "classes", "bookings", "staff"],
        "schema_description": (
            "Grand Clubhouse management system. "
            "facilities: sports and fitness facilities (gym, pool, tennis, squash, yoga, spin) with rates and hours. "
            "members: club members with Bronze/Silver/Gold/Platinum memberships. "
            "classes: fitness and swimming classes with instructor and schedule. "
            "bookings: facility reservations with date, time slot, and member. "
            "staff: instructors, trainers, and support staff."
        )
    }


# ==============================================================
# CUSTOMER 2 — AquaFit Swimming Club  (AquaFitDB)
# ==============================================================
def create_aquafit_data():
    db = client["AquaFitDB"]

    for col in ["pools", "swimmers", "sessions", "coaches", "lane_bookings"]:
        db[col].drop()
    print("  Cleared existing AquaFit collections")

    # Pools
    db.pools.insert_many([
        {
            "pool_id": "P01", "name": "Competition Pool", "lanes": 10,
            "length_meters": 50, "water_temp_celsius": 27,
            "operating_hours": {"weekday": "05:30-22:00", "weekend": "06:00-20:00"},
            "available": True
        },
        {
            "pool_id": "P02", "name": "Training Pool", "lanes": 6,
            "length_meters": 25, "water_temp_celsius": 29,
            "operating_hours": {"weekday": "06:00-22:00", "weekend": "07:00-19:00"},
            "available": True
        },
        {
            "pool_id": "P03", "name": "Kids Pool", "lanes": 0,
            "length_meters": 15, "water_temp_celsius": 30,
            "operating_hours": {"weekday": "09:00-18:00", "weekend": "09:00-17:00"},
            "available": True
        },
    ])
    print(f"  Created {db.pools.count_documents({})} pools")

    # Swimmers (10, all English names)
    db.swimmers.insert_many([
        {
            "swimmer_id": "SW001", "name": "Tom Fletcher", "age": 24,
            "level": "competitive", "strokes": ["freestyle", "butterfly"],
            "membership": "Elite", "monthly_fee": 120,
            "joined": "2024-03-01", "personal_best_100m": "52.4s"
        },
        {
            "swimmer_id": "SW002", "name": "Alice Cooper", "age": 17,
            "level": "intermediate", "strokes": ["freestyle", "backstroke"],
            "membership": "Junior", "monthly_fee": 75,
            "joined": "2024-07-15", "personal_best_100m": "1:08.2"
        },
        {
            "swimmer_id": "SW003", "name": "Jack Harrison", "age": 35,
            "level": "recreational", "strokes": ["freestyle"],
            "membership": "Standard", "monthly_fee": 55,
            "joined": "2025-01-10", "personal_best_100m": None
        },
        {
            "swimmer_id": "SW004", "name": "Grace Murphy", "age": 8,
            "level": "beginner", "strokes": [],
            "membership": "Kids", "monthly_fee": 40,
            "joined": "2025-11-01", "personal_best_100m": None
        },
        {
            "swimmer_id": "SW005", "name": "Noah Parker", "age": 21,
            "level": "competitive", "strokes": ["freestyle", "breaststroke", "backstroke"],
            "membership": "Elite", "monthly_fee": 120,
            "joined": "2023-09-01", "personal_best_100m": "55.1s"
        },
        {
            "swimmer_id": "SW006", "name": "Lily Evans", "age": 14,
            "level": "intermediate", "strokes": ["freestyle", "butterfly"],
            "membership": "Junior", "monthly_fee": 75,
            "joined": "2024-02-20", "personal_best_100m": "1:12.5"
        },
        {
            "swimmer_id": "SW007", "name": "Harry Wright", "age": 42,
            "level": "recreational", "strokes": ["freestyle", "breaststroke"],
            "membership": "Standard", "monthly_fee": 55,
            "joined": "2024-11-05", "personal_best_100m": None
        },
        {
            "swimmer_id": "SW008", "name": "Mia Scott", "age": 9,
            "level": "beginner", "strokes": ["freestyle"],
            "membership": "Kids", "monthly_fee": 40,
            "joined": "2025-09-01", "personal_best_100m": None
        },
        {
            "swimmer_id": "SW009", "name": "Ethan Powell", "age": 28,
            "level": "advanced", "strokes": ["freestyle", "butterfly", "backstroke"],
            "membership": "Elite", "monthly_fee": 120,
            "joined": "2023-05-15", "personal_best_100m": "49.8s"
        },
        {
            "swimmer_id": "SW010", "name": "Chloe Bailey", "age": 31,
            "level": "intermediate", "strokes": ["breaststroke", "freestyle"],
            "membership": "Standard", "monthly_fee": 55,
            "joined": "2024-06-10", "personal_best_100m": "1:18.0"
        },
    ])
    print(f"  Created {db.swimmers.count_documents({})} swimmers")

    # Coaches
    db.coaches.insert_many([
        {
            "coach_id": "C01", "name": "Mark Stevens", "specialty": "Competitive / Sprint",
            "certifications": ["FINA Level 3", "ASA Senior Coach"],
            "experience_years": 15, "available_hours": "05:30-14:00 weekdays"
        },
        {
            "coach_id": "C02", "name": "Claire Watson", "specialty": "Kids and Beginners",
            "certifications": ["Swim England Level 2", "Child Safeguarding"],
            "experience_years": 7, "available_hours": "09:00-17:00 daily"
        },
        {
            "coach_id": "C03", "name": "Peter Hughes", "specialty": "Fitness and Triathlon",
            "certifications": ["Triathlon Coach Level 2", "ASA Assistant Coach"],
            "experience_years": 9, "available_hours": "06:00-13:00 and 17:00-20:00"
        },
        {
            "coach_id": "C04", "name": "Rachel Green", "specialty": "Open Water and Endurance",
            "certifications": ["ASA Senior Coach", "Open Water Specialist"],
            "experience_years": 11, "available_hours": "07:00-15:00 weekdays"
        },
    ])
    print(f"  Created {db.coaches.count_documents({})} coaches")

    # Sessions
    today = datetime.now().strftime("%Y-%m-%d")
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    db.sessions.insert_many([
        {
            "session_id": "SES001", "name": "Early Bird Lap Swim",
            "pool_id": "P01", "coach_id": None,
            "date": today, "time": "05:30-07:00",
            "level": "all", "max_swimmers": 10, "enrolled": 7, "fee": 8
        },
        {
            "session_id": "SES002", "name": "Junior Training",
            "pool_id": "P02", "coach_id": "C01",
            "date": today, "time": "15:00-17:00",
            "level": "competitive", "max_swimmers": 8, "enrolled": 6, "fee": 18
        },
        {
            "session_id": "SES003", "name": "Kids Learn to Swim",
            "pool_id": "P03", "coach_id": "C02",
            "date": today, "time": "10:00-11:00",
            "level": "beginner", "max_swimmers": 8, "enrolled": 4, "fee": 14
        },
        {
            "session_id": "SES004", "name": "Triathlon Prep",
            "pool_id": "P01", "coach_id": "C03",
            "date": tomorrow, "time": "06:00-07:30",
            "level": "advanced", "max_swimmers": 6, "enrolled": 5, "fee": 20
        },
        {
            "session_id": "SES005", "name": "Open Lap Swim",
            "pool_id": "P02", "coach_id": None,
            "date": tomorrow, "time": "18:00-20:00",
            "level": "all", "max_swimmers": 12, "enrolled": 3, "fee": 7
        },
        {
            "session_id": "SES006", "name": "Endurance Training",
            "pool_id": "P01", "coach_id": "C04",
            "date": tomorrow, "time": "07:00-08:30",
            "level": "advanced", "max_swimmers": 8, "enrolled": 7, "fee": 22
        },
    ])
    print(f"  Created {db.sessions.count_documents({})} sessions")

    # Lane bookings
    db.lane_bookings.insert_many([
        {
            "booking_id": "LB001", "swimmer_id": "SW001", "swimmer_name": "Tom Fletcher",
            "pool_id": "P01", "lane": 1, "date": today,
            "time_slot": "05:30-07:00", "status": "confirmed", "fee": 8
        },
        {
            "booking_id": "LB002", "swimmer_id": "SW003", "swimmer_name": "Jack Harrison",
            "pool_id": "P02", "lane": 3, "date": tomorrow,
            "time_slot": "18:00-20:00", "status": "confirmed", "fee": 7
        },
        {
            "booking_id": "LB003", "swimmer_id": "SW009", "swimmer_name": "Ethan Powell",
            "pool_id": "P01", "lane": 2, "date": today,
            "time_slot": "05:30-07:00", "status": "confirmed", "fee": 8
        },
        {
            "booking_id": "LB004", "swimmer_id": "SW005", "swimmer_name": "Noah Parker",
            "pool_id": "P02", "lane": 1, "date": tomorrow,
            "time_slot": "07:00-08:30", "status": "confirmed", "fee": 22
        },
    ])
    print(f"  Created {db.lane_bookings.count_documents({})} lane bookings")

    return {
        "connection_string": PLATFORM_MONGO_URI,
        "database": "AquaFitDB",
        "collections": ["pools", "swimmers", "sessions", "coaches", "lane_bookings"],
        "schema_description": (
            "AquaFit Swimming Club management system. "
            "pools: swimming pools with lanes, temperature, and hours. "
            "swimmers: club members with skill levels (beginner/intermediate/competitive/elite) and memberships. "
            "sessions: scheduled swim sessions with coach and available spots. "
            "coaches: swim coaches with specialties and certifications. "
            "lane_bookings: individual lane reservations by swimmers."
        )
    }


# ==============================================================
# CUSTOMER 3 — Riverside Tennis Club  (TennisDB)
# ==============================================================
def create_tennis_data():
    db = client["TennisDB"]

    for col in ["courts", "members", "lessons", "match_bookings", "coaches"]:
        db[col].drop()
    print("  Cleared existing Tennis Club collections")

    # Courts
    db.courts.insert_many([
        {
            "court_id": "CT001", "name": "Centre Court", "surface": "grass",
            "indoor": False, "flood_lights": True,
            "operating_hours": {"weekday": "07:00-21:00", "weekend": "07:00-20:00"},
            "rate_per_hour": 25, "available": True
        },
        {
            "court_id": "CT002", "name": "Court 2", "surface": "clay",
            "indoor": False, "flood_lights": True,
            "operating_hours": {"weekday": "07:00-21:00", "weekend": "07:00-20:00"},
            "rate_per_hour": 20, "available": True
        },
        {
            "court_id": "CT003", "name": "Court 3", "surface": "clay",
            "indoor": False, "flood_lights": False,
            "operating_hours": {"weekday": "08:00-19:00", "weekend": "08:00-18:00"},
            "rate_per_hour": 15, "available": True
        },
        {
            "court_id": "CT004", "name": "Indoor Court", "surface": "hard",
            "indoor": True, "flood_lights": True,
            "operating_hours": {"weekday": "06:00-22:00", "weekend": "07:00-21:00"},
            "rate_per_hour": 30, "available": True
        },
    ])
    print(f"  Created {db.courts.count_documents({})} courts")

    # Members (10, all English names)
    db.members.insert_many([
        {
            "member_id": "TM001", "name": "Andrew Fletcher", "email": "andrew.fletcher@email.com",
            "phone": "+44-7922-301001", "membership_type": "Full",
            "skill_level": "advanced", "ranking": 3,
            "membership_expiry": (datetime.now() + timedelta(days=365)).isoformat(),
            "joined_date": "2021-05-10"
        },
        {
            "member_id": "TM002", "name": "Victoria Reed", "email": "victoria.reed@email.com",
            "phone": "+44-7922-301010", "membership_type": "Full",
            "skill_level": "intermediate", "ranking": 7,
            "membership_expiry": (datetime.now() + timedelta(days=200)).isoformat(),
            "joined_date": "2022-09-15"
        },
        {
            "member_id": "TM003", "name": "Benjamin Cole", "email": "benjamin.cole@email.com",
            "phone": "+44-7922-301020", "membership_type": "Junior",
            "skill_level": "beginner", "ranking": None,
            "membership_expiry": (datetime.now() + timedelta(days=300)).isoformat(),
            "joined_date": "2024-01-20"
        },
        {
            "member_id": "TM004", "name": "Natalie Brooks", "email": "natalie.brooks@email.com",
            "phone": "+44-7922-301030", "membership_type": "Social",
            "skill_level": "recreational", "ranking": None,
            "membership_expiry": (datetime.now() + timedelta(days=120)).isoformat(),
            "joined_date": "2024-03-01"
        },
        {
            "member_id": "TM005", "name": "Christopher Lane", "email": "chris.lane@email.com",
            "phone": "+44-7922-301040", "membership_type": "Full",
            "skill_level": "advanced", "ranking": 1,
            "membership_expiry": (datetime.now() + timedelta(days=365)).isoformat(),
            "joined_date": "2019-08-01"
        },
        {
            "member_id": "TM006", "name": "Eleanor Price", "email": "eleanor.price@email.com",
            "phone": "+44-7922-301050", "membership_type": "Full",
            "skill_level": "intermediate", "ranking": 5,
            "membership_expiry": (datetime.now() + timedelta(days=250)).isoformat(),
            "joined_date": "2022-02-14"
        },
        {
            "member_id": "TM007", "name": "Samuel Grant", "email": "samuel.grant@email.com",
            "phone": "+44-7922-301060", "membership_type": "Junior",
            "skill_level": "intermediate", "ranking": 2,
            "membership_expiry": (datetime.now() + timedelta(days=180)).isoformat(),
            "joined_date": "2023-06-01"
        },
        {
            "member_id": "TM008", "name": "Isabelle Hunt", "email": "isabelle.hunt@email.com",
            "phone": "+44-7922-301070", "membership_type": "Social",
            "skill_level": "beginner", "ranking": None,
            "membership_expiry": (datetime.now() + timedelta(days=90)).isoformat(),
            "joined_date": "2025-01-05"
        },
        {
            "member_id": "TM009", "name": "Daniel Marsh", "email": "daniel.marsh@email.com",
            "phone": "+44-7922-301080", "membership_type": "Full",
            "skill_level": "advanced", "ranking": 4,
            "membership_expiry": (datetime.now() + timedelta(days=365)).isoformat(),
            "joined_date": "2020-11-30"
        },
        {
            "member_id": "TM010", "name": "Olivia Stone", "email": "olivia.stone@email.com",
            "phone": "+44-7922-301090", "membership_type": "Full",
            "skill_level": "intermediate", "ranking": 6,
            "membership_expiry": (datetime.now() + timedelta(days=150)).isoformat(),
            "joined_date": "2023-03-20"
        },
    ])
    print(f"  Created {db.members.count_documents({})} members")

    # Coaches
    db.coaches.insert_many([
        {
            "coach_id": "TC01", "name": "James Wilkinson", "role": "Head Coach",
            "email": "james.wilkinson@riversidetennis.com",
            "certifications": ["LTA Level 4", "ITF Coach"],
            "experience_years": 18, "speciality": "Advanced competitive play"
        },
        {
            "coach_id": "TC02", "name": "Emma Ford", "role": "Junior Coach",
            "email": "emma.ford@riversidetennis.com",
            "certifications": ["LTA Level 2", "Child Protection"],
            "experience_years": 6, "speciality": "Junior development and beginners"
        },
        {
            "coach_id": "TC03", "name": "Paul Griffiths", "role": "Fitness Coach",
            "email": "paul.griffiths@riversidetennis.com",
            "certifications": ["LTA Level 3", "S&C Certified"],
            "experience_years": 10, "speciality": "Fitness, footwork, and match strategy"
        },
    ])
    print(f"  Created {db.coaches.count_documents({})} coaches")

    # Lessons
    db.lessons.insert_many([
        {
            "lesson_id": "LS001", "name": "Beginner Group Lesson", "coach_id": "TC02",
            "court_id": "CT003",
            "schedule": {"days": ["Saturday"], "time": "09:00-10:00"},
            "max_students": 6, "enrolled": 4, "fee_per_session": 18
        },
        {
            "lesson_id": "LS002", "name": "Junior Development", "coach_id": "TC02",
            "court_id": "CT002",
            "schedule": {"days": ["Tuesday", "Thursday"], "time": "16:00-17:00"},
            "max_students": 8, "enrolled": 7, "fee_per_session": 16
        },
        {
            "lesson_id": "LS003", "name": "Advanced Tactics", "coach_id": "TC01",
            "court_id": "CT001",
            "schedule": {"days": ["Monday", "Wednesday"], "time": "18:00-19:30"},
            "max_students": 4, "enrolled": 4, "fee_per_session": 35
        },
        {
            "lesson_id": "LS004", "name": "Cardio Tennis", "coach_id": "TC03",
            "court_id": "CT004",
            "schedule": {"days": ["Monday", "Wednesday", "Friday"], "time": "07:00-08:00"},
            "max_students": 10, "enrolled": 8, "fee_per_session": 20
        },
    ])
    print(f"  Created {db.lessons.count_documents({})} lessons")

    # Match bookings
    today = datetime.now().strftime("%Y-%m-%d")
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    db.match_bookings.insert_many([
        {
            "booking_id": "MB001", "member_id": "TM001", "member_name": "Andrew Fletcher",
            "court_id": "CT001", "date": today, "time_slot": "10:00-11:00",
            "opponent": "TM005", "opponent_name": "Christopher Lane",
            "status": "confirmed", "fee": 25
        },
        {
            "booking_id": "MB002", "member_id": "TM002", "member_name": "Victoria Reed",
            "court_id": "CT002", "date": today, "time_slot": "14:00-15:00",
            "opponent": "TM006", "opponent_name": "Eleanor Price",
            "status": "confirmed", "fee": 20
        },
        {
            "booking_id": "MB003", "member_id": "TM009", "member_name": "Daniel Marsh",
            "court_id": "CT004", "date": tomorrow, "time_slot": "09:00-10:00",
            "opponent": None, "opponent_name": None,
            "status": "confirmed", "fee": 30
        },
        {
            "booking_id": "MB004", "member_id": "TM005", "member_name": "Christopher Lane",
            "court_id": "CT001", "date": tomorrow, "time_slot": "11:00-12:00",
            "opponent": "TM009", "opponent_name": "Daniel Marsh",
            "status": "confirmed", "fee": 25
        },
    ])
    print(f"  Created {db.match_bookings.count_documents({})} match bookings")

    return {
        "connection_string": PLATFORM_MONGO_URI,
        "database": "TennisDB",
        "collections": ["courts", "members", "lessons", "match_bookings", "coaches"],
        "schema_description": (
            "Riverside Tennis Club management system. "
            "courts: tennis courts with surface type (grass/clay/hard), indoor/outdoor, and rates. "
            "members: club members with Full/Junior/Social memberships, skill levels, and rankings. "
            "lessons: group and individual coaching sessions with coach and schedule. "
            "match_bookings: court reservations for matches and practice. "
            "coaches: certified tennis coaches with specialities."
        )
    }


# ==============================================================
# CUSTOMER 4 — City Fitness Center  (FitnessDB)
# ==============================================================
def create_fitness_data():
    db = client["FitnessDB"]

    for col in ["zones", "members", "personal_training", "subscriptions", "trainers"]:
        db[col].drop()
    print("  Cleared existing Fitness Center collections")

    # Zones
    db.zones.insert_many([
        {
            "zone_id": "ZN001", "name": "Cardio Zone", "type": "cardio",
            "equipment": ["Treadmills x12", "Ellipticals x8", "Bikes x10", "Rowing Machines x4"],
            "capacity": 40, "operating_hours": {"weekday": "05:30-23:00", "weekend": "07:00-21:00"}
        },
        {
            "zone_id": "ZN002", "name": "Free Weights Area", "type": "strength",
            "equipment": ["Dumbbells 2-50kg", "Barbells", "Squat Racks x6", "Benches x8"],
            "capacity": 30, "operating_hours": {"weekday": "05:30-23:00", "weekend": "07:00-21:00"}
        },
        {
            "zone_id": "ZN003", "name": "Functional Training Zone", "type": "functional",
            "equipment": ["Kettlebells", "Battle Ropes", "TRX Suspension", "Plyometric Boxes"],
            "capacity": 20, "operating_hours": {"weekday": "06:00-22:00", "weekend": "07:00-20:00"}
        },
        {
            "zone_id": "ZN004", "name": "Group Exercise Studio", "type": "studio",
            "equipment": ["Sound System", "Mirrors", "Spin Bikes x20", "Step Platforms"],
            "capacity": 35, "operating_hours": {"weekday": "06:00-21:00", "weekend": "08:00-18:00"}
        },
        {
            "zone_id": "ZN005", "name": "Stretching & Recovery", "type": "recovery",
            "equipment": ["Foam Rollers", "Massage Guns", "Stretching Mats", "Resistance Bands"],
            "capacity": 15, "operating_hours": {"weekday": "05:30-23:00", "weekend": "07:00-21:00"}
        },
    ])
    print(f"  Created {db.zones.count_documents({})} zones")

    # Members (10, all English names)
    db.members.insert_many([
        {
            "member_id": "FM001", "name": "Nathan Price", "email": "nathan.price@email.com",
            "phone": "+44-7933-401001", "goal": "muscle gain",
            "subscription_type": "Premium", "monthly_fee": 55,
            "joined_date": "2023-02-01", "personal_trainer_id": "PT001"
        },
        {
            "member_id": "FM002", "name": "Jessica Long", "email": "jessica.long@email.com",
            "phone": "+44-7933-401010", "goal": "weight loss",
            "subscription_type": "Standard", "monthly_fee": 35,
            "joined_date": "2023-11-15", "personal_trainer_id": None
        },
        {
            "member_id": "FM003", "name": "Ryan Hughes", "email": "ryan.hughes@email.com",
            "phone": "+44-7933-401020", "goal": "general fitness",
            "subscription_type": "Standard", "monthly_fee": 35,
            "joined_date": "2024-01-08", "personal_trainer_id": None
        },
        {
            "member_id": "FM004", "name": "Amelia Fox", "email": "amelia.fox@email.com",
            "phone": "+44-7933-401030", "goal": "marathon training",
            "subscription_type": "Premium", "monthly_fee": 55,
            "joined_date": "2022-08-20", "personal_trainer_id": "PT002"
        },
        {
            "member_id": "FM005", "name": "Connor Walsh", "email": "connor.walsh@email.com",
            "phone": "+44-7933-401040", "goal": "muscle gain",
            "subscription_type": "Premium", "monthly_fee": 55,
            "joined_date": "2023-05-12", "personal_trainer_id": "PT001"
        },
        {
            "member_id": "FM006", "name": "Hannah Mills", "email": "hannah.mills@email.com",
            "phone": "+44-7933-401050", "goal": "weight loss",
            "subscription_type": "Basic", "monthly_fee": 22,
            "joined_date": "2025-01-03", "personal_trainer_id": None
        },
        {
            "member_id": "FM007", "name": "Liam Burton", "email": "liam.burton@email.com",
            "phone": "+44-7933-401060", "goal": "powerlifting",
            "subscription_type": "Premium", "monthly_fee": 55,
            "joined_date": "2021-06-30", "personal_trainer_id": "PT003"
        },
        {
            "member_id": "FM008", "name": "Ella Pearce", "email": "ella.pearce@email.com",
            "phone": "+44-7933-401070", "goal": "general fitness",
            "subscription_type": "Standard", "monthly_fee": 35,
            "joined_date": "2024-04-17", "personal_trainer_id": None
        },
        {
            "member_id": "FM009", "name": "Dylan Shaw", "email": "dylan.shaw@email.com",
            "phone": "+44-7933-401080", "goal": "athletic performance",
            "subscription_type": "Premium", "monthly_fee": 55,
            "joined_date": "2023-07-22", "personal_trainer_id": "PT002"
        },
        {
            "member_id": "FM010", "name": "Phoebe Lawson", "email": "phoebe.lawson@email.com",
            "phone": "+44-7933-401090", "goal": "flexibility and wellness",
            "subscription_type": "Basic", "monthly_fee": 22,
            "joined_date": "2024-09-01", "personal_trainer_id": None
        },
    ])
    print(f"  Created {db.members.count_documents({})} members")

    # Personal trainers
    db.trainers.insert_many([
        {
            "trainer_id": "PT001", "name": "Marcus Reid", "speciality": "Strength and Hypertrophy",
            "email": "marcus.reid@cityfitness.com",
            "certifications": ["NASM-CPT", "NSCA-CSCS"],
            "experience_years": 9, "clients": ["FM001", "FM005"],
            "available_slots": ["Morning", "Evening"]
        },
        {
            "trainer_id": "PT002", "name": "Laura Knight", "speciality": "Endurance and Running",
            "email": "laura.knight@cityfitness.com",
            "certifications": ["ACE-CPT", "RRCA Running Coach"],
            "experience_years": 7, "clients": ["FM004", "FM009"],
            "available_slots": ["Morning", "Afternoon"]
        },
        {
            "trainer_id": "PT003", "name": "Steve Barker", "speciality": "Powerlifting and Olympic Lifting",
            "email": "steve.barker@cityfitness.com",
            "certifications": ["NASM-CPT", "USAW Level 1"],
            "experience_years": 14, "clients": ["FM007"],
            "available_slots": ["Afternoon", "Evening"]
        },
    ])
    print(f"  Created {db.trainers.count_documents({})} trainers")

    # Personal training sessions
    today = datetime.now().strftime("%Y-%m-%d")
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    db.personal_training.insert_many([
        {
            "session_id": "PS001", "member_id": "FM001", "member_name": "Nathan Price",
            "trainer_id": "PT001", "trainer_name": "Marcus Reid",
            "date": today, "time": "07:00-08:00", "focus": "Upper body strength",
            "status": "confirmed", "fee": 65
        },
        {
            "session_id": "PS002", "member_id": "FM004", "member_name": "Amelia Fox",
            "trainer_id": "PT002", "trainer_name": "Laura Knight",
            "date": today, "time": "06:00-07:00", "focus": "Long run debrief and interval planning",
            "status": "confirmed", "fee": 60
        },
        {
            "session_id": "PS003", "member_id": "FM007", "member_name": "Liam Burton",
            "trainer_id": "PT003", "trainer_name": "Steve Barker",
            "date": tomorrow, "time": "17:00-18:00", "focus": "Deadlift technique",
            "status": "confirmed", "fee": 70
        },
        {
            "session_id": "PS004", "member_id": "FM005", "member_name": "Connor Walsh",
            "trainer_id": "PT001", "trainer_name": "Marcus Reid",
            "date": tomorrow, "time": "08:00-09:00", "focus": "Leg day and core",
            "status": "confirmed", "fee": 65
        },
    ])
    print(f"  Created {db.personal_training.count_documents({})} PT sessions")

    # Subscriptions
    db.subscriptions.insert_many([
        {
            "plan": "Basic", "price_monthly": 22,
            "features": ["Gym floor access", "Changing rooms", "2 guest passes/month"]
        },
        {
            "plan": "Standard", "price_monthly": 35,
            "features": ["Gym floor access", "Group classes x8/month", "Sauna", "Towel service"]
        },
        {
            "plan": "Premium", "price_monthly": 55,
            "features": ["Unlimited gym access", "Unlimited group classes", "Sauna", "1 PT session/month", "Nutrition consultation"]
        },
    ])
    print(f"  Created {db.subscriptions.count_documents({})} subscription plans")

    return {
        "connection_string": PLATFORM_MONGO_URI,
        "database": "FitnessDB",
        "collections": ["zones", "members", "personal_training", "subscriptions", "trainers"],
        "schema_description": (
            "City Fitness Center management system. "
            "zones: gym areas (cardio, free weights, functional, studio, recovery) with equipment lists. "
            "members: gym members with Basic/Standard/Premium subscriptions and fitness goals. "
            "personal_training: one-on-one PT sessions with trainer, focus area, and fee. "
            "subscriptions: membership plan tiers and their included features. "
            "trainers: personal trainers with specialities, certifications, and current clients."
        )
    }


# ==============================================================
# REGISTER ALL CUSTOMERS IN THE PLATFORM
# ==============================================================
def register_customers(configs):
    db = client[PLATFORM_DB]

    db.api_keys.drop()
    db.customers.drop()

    def make_key():
        return f"va_{secrets.token_urlsafe(32)}"

    customers = [
        {"customer_id": "CUST_CLUBHOUSE", "name": "Grand Clubhouse",
         "email": "admin@grandclubhouse.com", "api_key": make_key(), "db_config": configs[0]},
        {"customer_id": "CUST_AQUAFIT",   "name": "AquaFit Swimming Club",
         "email": "admin@aquafit.com",       "api_key": make_key(), "db_config": configs[1]},
        {"customer_id": "CUST_TENNIS",    "name": "Riverside Tennis Club",
         "email": "admin@riversidetennis.com", "api_key": make_key(), "db_config": configs[2]},
        {"customer_id": "CUST_FITNESS",   "name": "City Fitness Center",
         "email": "admin@cityfitness.com",  "api_key": make_key(), "db_config": configs[3]},
    ]

    for c in customers:
        db.customers.insert_one({
            "customer_id": c["customer_id"],
            "name": c["name"],
            "email": c["email"],
            "created_at": datetime.now().isoformat()
        })
        db.api_keys.insert_one({
            "key": c["api_key"],
            "customer_id": c["customer_id"],
            "customer_name": c["name"],
            "db_config": c["db_config"],
            "active": True,
            "usage_count": 0,
            "last_used": None,
            "created_at": datetime.now().isoformat()
        })

    return customers


# ==============================================================
# MAIN
# ==============================================================
if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("Creating demo data for 4 customers...")
    print("=" * 60)

    print("\nCustomer 1: Grand Clubhouse")
    print("-" * 40)
    cfg1 = create_clubhouse_data()

    print("\nCustomer 2: AquaFit Swimming Club")
    print("-" * 40)
    cfg2 = create_aquafit_data()

    print("\nCustomer 3: Riverside Tennis Club")
    print("-" * 40)
    cfg3 = create_tennis_data()

    print("\nCustomer 4: City Fitness Center")
    print("-" * 40)
    cfg4 = create_fitness_data()

    print("\nRegistering customers and generating API keys...")
    print("-" * 40)
    customers = register_customers([cfg1, cfg2, cfg3, cfg4])

    print("\n" + "=" * 60)
    print("DONE - Here are your API keys:")
    print("=" * 60)
    for c in customers:
        print(f"\nCustomer : {c['name']}")
        print(f"Email    : {c['email']}")
        print(f"API Key  : {c['api_key']}")
        print(f"Database : {c['db_config']['database']}")

    print("\n" + "=" * 60)
    print("Quick test (Grand Clubhouse):")
    print("=" * 60)
    print(f"""
curl -X POST http://localhost:5001/api/agent/query \\
  -H "X-API-Key: {customers[0]['api_key']}" \\
  -H "Content-Type: application/json" \\
  -d '{{"query": "Which members have a Gold membership?"}}'
""")
