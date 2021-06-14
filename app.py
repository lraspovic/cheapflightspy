from flask import Flask, render_template, request
from flask_sqlalchemy import SQLAlchemy
from amadeus import Client, ResponseError, Location
from flask_table import Table, Col
import json
import urllib.request
from geopy.distance import geodesic


app = Flask(__name__)

app.debug = False
app.config['SQLALCHEMY_DATABASE_URI'] = 'postgres://usflwvgarwfglx:71a3c6999b27fd855d0040ba890a56c25b32993d6bddb416bfff8f6cf6601713@ec2-34-230-115-172.compute-1.amazonaws.com:5432/d7jtad8lopgmme'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

amadeus = Client(
    client_id='tiIPMhrKnPpGH5pzTyCRSJ8ARCP92e4B',
    client_secret='FUQETphivhDTrpqz'
)

# Create db table "Flight"
class Flight(db.Model):
    __tablename__ = 'Flight'
    id = db.Column(db.Integer, primary_key=True)
    departure_airport = db.Column(db.String(200))
    destination_airport = db.Column(db.String(200))
    departure_date = db.Column(db.Date)
    return_date = db.Column(db.Date)
    layovers_to = db.Column(db.Integer)
    layovers_back = db.Column(db.Integer)
    n_passengers = db.Column(db.Integer)
    currency = db.Column(db.String(3))
    price = db.Column(db.Float)
    distance = db.Column(db.Integer)
    search_string = db.Column(db.String(500))

    def __init__(self, departure_airport, destination_airport, departure_date, return_date, layovers_to, layovers_back,
     n_passengers, currency, price, distance, search_string):
        self.departure_airport = departure_airport
        self.destination_airport = destination_airport
        self.departure_date = departure_date
        self.return_date = return_date
        self.layovers_to = layovers_to
        self.layovers_back = layovers_back
        self.n_passengers = n_passengers
        self.currency = currency
        self.price = price
        self.distance = distance
        self.search_string = search_string

# Create html table
class ItemTable(Table):
    departure_airport = Col('From')
    destination_airport = Col('To')
    departure_date = Col('Departure date')
    return_date = Col('Return date')
    layovers_to = Col('Layovers to destination')
    layovers_back = Col('Layovers back')
    n_passengers = Col('Passengers')
    currency = Col('Currency')
    price = Col('Total price')
    distance = Col('Distance (km)')

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/autocomplete', methods=['GET'])
def autocomplete():
    search = request.args.get('term')
    data = amadeus.reference_data.locations.get(keyword=search, subType=Location.ANY).data
    return get_city_airport_list(data)

@app.route('/submit', methods=['POST'])
def submit():
    # Get form data
    if request.method == 'POST':
        departure_airport = request.form['dp_airport'].upper()
        destination_airport = request.form['ds_airport'].upper()
        departure_date = request.form['departure_date']
        return_date = request.form['return_date']
        n_passengers = request.form['n_passengers']
        currency = request.form['currency']
        url = get_url(departure_airport, destination_airport, departure_date, return_date, n_passengers, currency)

        # Check for emtpy fields
        if departure_airport == '' or destination_airport == '':
            return render_template('index.html', message='Please enter required fields')

        # Check if data is already in db
        if db.session.query(Flight).filter(Flight.search_string == url).count() == 0:
            table, flights = get_flights(departure_airport, destination_airport, departure_date, return_date, n_passengers, currency)
            if table is None:
                return render_template('index.html', message='No flights found')
            # Add data to db
            add_to_db(db, flights, url)
        else:
            # Retrieve data from db
            db_flights = []
            querry_data = db.session.query(Flight).filter(Flight.search_string == url).all()
            for db_data in querry_data:
                db_flight = create_flight_dict(db_data.departure_airport, db_data.destination_airport, db_data.departure_date, db_data.return_date, db_data.n_passengers, [db_data.layovers_to, db_data.layovers_back], db_data.price, db_data.currency, db_data.distance)
                db_flights.append(db_flight)
            table = ItemTable(db_flights)  
        return render_template('results.html', table=table)

def get_url(departure_airport, destination_airport, departure_date, return_date, n_passengers, currency):
    ''' Creates and returns search url 
    '''
    url = f'https://test.api.amadeus.com/v2/shopping/flight-offers?originLocationCode={departure_airport}&destinationLocationCode={destination_airport}&departureDate={departure_date}&returnDate={return_date}&adults={n_passengers}&currencyCode={currency}'
    return url

def get_city_airport_list(data):
    result = []
    for i, val in enumerate(data):
        result.append(data[i]['iataCode']+', '+data[i]['name'])
    result = list(dict.fromkeys(result))
    return json.dumps(result)
  
def get_flights(departure_airport, destination_airport, departure_date, return_date, n_passengers, currency):
    '''Retrieves all flight data
    '''
    flights = []
    departure_airport, departure_airport_name = get_airport_data(departure_airport)
    destination_airport, destination_airport_name = get_airport_data(destination_airport)
    try:
        # API call
        response = amadeus.shopping.flight_offers_search.get(
            originLocationCode=departure_airport,
            destinationLocationCode=destination_airport,
            departureDate=departure_date,
            returnDate=return_date,
            adults=n_passengers,
            currencyCode=currency)

        # Check if any flights exist
        if len(response.data) == 0:
            return None, None
        
        if not departure_airport_name:
            departure_airport_data = get_airport_data_iatageo(departure_airport)
            departure_airport_name = departure_airport_data[0]  
            departure_airport_coords = departure_airport_data[1:]
        else:
            departure_airport_coords = get_airport_coords(departure_airport, departure_airport_name)
        if not destination_airport_name:
            destination_airport_data = get_airport_data_iatageo(destination_airport)
            destination_airport_name = destination_airport_data[0]  
            destination_airport_coords = destination_airport_data[1:]
        else:
            destination_airport_coords = get_airport_coords(destination_airport, destination_airport_name)

        distance = int(geodesic(departure_airport_coords, destination_airport_coords).km)

        for flight in response.data:
            layovers = get_number_of_layovers(flight)
            total_price = get_total_price(flight)
            flight_dict = create_flight_dict(departure_airport_name, destination_airport_name, departure_date, return_date, n_passengers, layovers, total_price, currency, distance)
            flights.append(flight_dict)
        
        # Remove duplicate flights
        unique_flights = list(map(dict, set(tuple(sorted(f.items())) for f in flights)))
        unique_flights_sorted = sorted(unique_flights, key = lambda i: i['price'])

        table = ItemTable(unique_flights_sorted)
        return table, unique_flights_sorted
    except ResponseError as error:
        print(error)
        return None, None

def add_to_db(db, data, url):
    for flight in data:
        flight_obj = Flight(flight['departure_airport'], flight['destination_airport'], flight['departure_date'], flight['return_date'],
            flight['layovers_to'], flight['layovers_back'], flight['n_passengers'], flight['currency'], flight['price'], flight['distance'],
            url )
        db.session.add(flight_obj)
        db.session.commit()

def create_flight_dict(departure_airport_name, destination_airport_name, departure_date, return_date, n_passengers, layovers, total_price, currency, distance):
    return dict(departure_airport=departure_airport_name, destination_airport=destination_airport_name, departure_date=departure_date, return_date=return_date, n_passengers=n_passengers, layovers_to=layovers[0], layovers_back=layovers[1], price=total_price, currency=currency, distance=distance)

def get_airport_data(data):
    # Check if autocomplete was used
    if len(data) > 3:
        return data[:3], data[5:]
    return data, None

def get_number_of_layovers(data):
    journey_to = data['itineraries'][0]
    journey_back = data['itineraries'][1]
    journey_to_layovers = len(journey_to['segments'])
    journey_back_layovers = len(journey_back['segments'])
    return journey_to_layovers, journey_back_layovers

def get_total_price(data):
    return float(data['price']['grandTotal'])

def get_airport_data_iatageo(iataCode):
    '''Searches airport name and location(lat, long)
    '''
    try:
        with urllib.request.urlopen(f'http://iatageo.com/getLatLng/{iataCode}') as res:
            res_json = json.loads(res.read())
            return res_json['name'], res_json['latitude'], res_json['longitude']
    except urllib.error.HTTPError as exception:
        return None

def get_airport_coords(iataCode, airportName):
    data = amadeus.reference_data.locations.get(keyword=airportName, subType=Location.ANY).data
    for fli in data:
        if fli['name'] == airportName and fli['iataCode'] == iataCode:

            return fli['geoCode']['latitude'], fli['geoCode']['longitude'],

if __name__ == '__main__':
    app.run()
