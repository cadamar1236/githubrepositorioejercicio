from flask import Flask, render_template, request, jsonify, session
from exani import generate_questions_exani, check_answer_exani, generate_new_questions_exani
from baccaulareat import generate_solutions_bac, retrieve_documents_bac, extract_relevant_context_bac
from langchain_community.chat_models import ChatDeepInfra
import os
from datetime import datetime, timezone
import logging
import datetime
from models import db, User 
import stripe
from elasticsearch import Elasticsearch
from langchain.prompts import ChatPromptTemplate
from flask import Flask, render_template, redirect, url_for, request, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager, current_user, login_user, logout_user, login_required, current_user
# Ruta inicial: Página principal para seleccionar el tipo de examen
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from models import db, User  # Importa db y User desde models.py
import stripe
import os
from flask_caching import Cache

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your_secret_key'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///site.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Cache configuration
app.config['CACHE_TYPE'] = 'simple'
db.init_app(app)  # Inicializa db con la aplicación
migrate = Migrate(app, db)  # Configura Flask-Migrate con tu app y db

login_manager = LoginManager()
login_manager.init_app(app)  # Asegúrate de inicializar el LoginManager con la app
login_manager.login_view = 'login'  # Página de inicio de sesión

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# Aquí puedes agregar tus rutas y lógica de la aplicación

# Código para crear las tablas en el contexto de la aplicación
with app.app_context():
    db.create_all()

# Token de API
os.environ["DEEPINFRA_API_TOKEN"] = "gtnKXw1ytDsD7DmCSsv2vwdXSW7IBJ5H"



# Set your secret key. Remember to switch to your live secret key in production!
stripe.api_key = 'sk_test_51Pr14b2K3oWETT3EMYe9NiKElssrbGmCHpxdUefcuaXLRkKyya5neMrK4jDzd2qh7GUhYZRQT8wqDaiGB2qtg2Md00fbj6TZqF'


@app.route('/', methods=['GET', 'POST'])
def index():
    if current_user.is_authenticated:
        # Refetch the current user to ensure the subscription type is updated
        user = User.query.get(current_user.id)
        subscription_type = user.subscription_type
    else:
        subscription_type = None

    if request.method == 'POST':
        exam_type = request.form.get('exam_type')
        if exam_type:
            return redirect('/select_exam')

    return render_template('index.html', subscription_type=subscription_type)


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        email = request.form['email']
        password = request.form['password']
        subscription_type = request.form['subscription_type']

        new_user = User(username=username, email=email, subscription_type='free')
        new_user.set_password(password)
        db.session.add(new_user)
        db.session.commit()

        login_user(new_user)

        if subscription_type in ['pro', 'premium']:
            # Redirigir a la página de pago
            return redirect(url_for('subscribe', plan=subscription_type))
        else:
            # Si es 'free', redirigir al índice
            flash('Te has registrado correctamente', 'success')
            return redirect(url_for('index'))

    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = User.query.filter_by(username=username).first()
        
        if user is None or not user.check_password(password):
            flash('Usuario o contraseña incorrectos', 'danger')
            return redirect(url_for('login'))

        login_user(user)
        flash('Has iniciado sesión correctamente', 'success')
        return redirect(url_for('index'))

    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Has cerrado sesión', 'success')
    return redirect(url_for('index'))

@app.route('/subscribe', methods=['GET', 'POST'])
@login_required
def subscribe():
    plan = request.args.get('plan', 'pro')  # Por defecto, usa 'pro' si no se especifica
    payment_links = {
        'premium': 'https://buy.stripe.com/test_28o8xO2p8aXmeeA8wx',  # Enlace para Premium
        'pro': 'https://buy.stripe.com/test_28o8xO2p8aXmeeA8wx',      # Enlace para Pro
    }

    if request.method == 'POST':
        subscription_type = request.form['subscription_type']
        if subscription_type not in payment_links:
            flash('Tipo de suscripción inválido', 'danger')
            return redirect(url_for('subscribe'))

        # Almacenamos el tipo de suscripción en la sesión del usuario
        session['pending_subscription_type'] = subscription_type

        return redirect(payment_links[subscription_type])

    return render_template('subscribe.html', selected_plan=plan)

@app.route('/webhook', methods=['POST'])
def stripe_webhook():
    payload = request.get_data(as_text=True)
    sig_header = request.headers.get('Stripe-Signature')
    endpoint_secret = 'whsec_iEQcZb38URJgh3gLtkmkWnRWm2BMA72e'

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, endpoint_secret)
        print(f"Received event: {event['type']}")  # Debugging line
    except ValueError as e:
        print(f"Error: {str(e)}")
        return '', 400
    except stripe.error.SignatureVerificationError as e:
        print(f"Signature error: {str(e)}")
        return '', 400

    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']
        handle_checkout_session(session)

    return '', 200

from flask_login import current_user
from datetime import datetime, timezone

cache = Cache(app)

def handle_checkout_session(session):
    customer_email = session.get('customer_details', {}).get('email')
    user = User.query.filter_by(email=customer_email).first()
    
    if not user:
        print(f"No user found with email: {customer_email}")
        return
    
    print(f"User found: {user.username}, current subscription: {user.subscription_type}")

    try:
        subscription_type = session.get('pending_subscription_type', 'pro')  # Default to 'pro'
        user.subscription_type = subscription_type
        user.subscription_start = datetime.now(timezone.utc)
        user.stripe_subscription_id = session.get('subscription')
        
        db.session.commit()
        print(f"User {user.username} subscription updated to: {user.subscription_type}")
        
        session.pop('pending_subscription_type', None)
        
        # Clear cache for this user
        cache.delete(f"user_{user.id}")
        
        # Update current_user session to reflect changes
        if current_user.is_authenticated and current_user.id == user.id:
            login_user(user, remember=True)  # This updates the session with the latest user info
            
    except Exception as e:
        print(f"Error updating subscription: {e}")
        db.session.rollback()


from datetime import datetime, timezone
import stripe

def log_request(func):
    def wrapper(*args, **kwargs):
        print(f"Request method: {request.method}, Path: {request.path}")
        return func(*args, **kwargs)
    return wrapper

@app.route('/cancel_subscription', methods=['POST'])
@login_required
@log_request
def cancel_subscription():
    print(f"Autenticado: {current_user.is_authenticated}")
    print(f"Usuario: {current_user.username if current_user.is_authenticated else 'No autenticado'}")
    print("Entrando a cancelar suscripción")  # Verificar que se está entrando en la función
    user = current_user

    if user.stripe_subscription_id:
        print(f"Intentando cancelar suscripción con ID: {user.stripe_subscription_id}")  # Verificar ID de Stripe
        try:
            # Intentar cancelar la suscripción en Stripe
            stripe.Subscription.delete(user.stripe_subscription_id)
            print("Suscripción cancelada en Stripe")  # Confirmar cancelación en Stripe
            
            # Actualizar usuario en la base de datos
            user.subscription_type = 'free'
            user.stripe_subscription_id = None
            user.subscription_start = None
            user.subscription_end = datetime.datetime.now(datetime.timezone.utc)
            db.session.commit()
            print("Suscripción actualizada en la base de datos")  # Confirmar actualización en DB
            
            flash('Tu suscripción ha sido cancelada exitosamente. Ahora tienes una cuenta gratuita.', 'success')
        except stripe.error.StripeError as e:
            print(f"Error al cancelar la suscripción en Stripe: {str(e)}")  # Log de error
            flash(f'Ocurrió un error al cancelar tu suscripción en Stripe: {str(e)}', 'danger')
            return redirect(url_for('profile'))
    else:
        # Si no hay ID de suscripción de Stripe, solo actualizamos la base de datos
        print("No se encontró un ID de suscripción en Stripe, actualizando base de datos")  # Log de fallback
        user.subscription_type = 'free'
        user.subscription_start = None
        user.subscription_end = datetime.datetime.now(datetime.timezone.utc)
        db.session.commit()
        flash('Tu suscripción ha sido cancelada. Ahora tienes una cuenta gratuita.', 'success')

    return redirect(url_for('profile'))


from flask import render_template, redirect, url_for, flash
from flask_login import current_user, login_required
from datetime import datetime

@app.route('/profile')
@login_required
def profile():
    # Verifica la suscripción actual del usuario
    subscription_type = current_user.subscription_type
    subscription_start = current_user.subscription_start

    # Renderiza una plantilla con la información de la suscripción
    return render_template('profile.html', subscription_type=subscription_type, subscription_start=subscription_start)


@app.route('/payment_success')
@login_required
def payment_success():
    flash('¡Tu pago ha sido exitoso! Tu suscripción ha sido actualizada.', 'success')
    return render_template('success.html')

@app.route('/payment_cancel')
@login_required
def payment_cancel():
    flash('Tu pago ha sido cancelado.', 'danger')
    return render_template('cancel.html')



@app.route('/select_exam', methods=['POST'])
def select_exam():
    exam_type = request.form.get('exam_type')
    if not exam_type:
        return "No se ha seleccionado ningún examen", 400
    # Procesar el tipo de examen
    return render_template('speciality.html', exam_type=exam_type)


def format_solutions(solutions_text):
    solutions_raw = solutions_text.split("\n\n")
    formatted_solutions = []

    for raw_solution in solutions_raw:
        title_match = re.search(r'^\*\*(.*?)\*\*', raw_solution)
        title = title_match.group(1) if title_match else "Solución"
        text = re.sub(r'^\*\*(.*?)\*\*', '', raw_solution).strip()

        formatted_solutions.append({
            "title": title,
            "text": text,
            "note": None
        })

    return formatted_solutions

@app.route('/generate_exam', methods=['POST'])
def generate_exam():
    exam_type = request.form['exam_type']
    num_items = int(request.form['num_items'])
    chat = ChatDeepInfra(model="meta-llama/Meta-Llama-3-8B-Instruct", max_tokens=4000)

    if exam_type == "exani_ii":
        segmento = request.form['segmento']
        asignatura = request.form['asignatura']
        questions = generate_questions_exani(chat, num_items, segmento, asignatura)

        # Incrementa el contador de preguntas para el usuario actual
        current_user.increment_questions()
        db.session.commit()  # Asegúrate de guardar los cambios en la base de datos

        return render_template('quiz.html', questions=questions)

    elif exam_type == "baccalaureat":
        speciality = request.form['speciality']
        es = Elasticsearch(
            cloud_id="d6ad8b393b364990a49e2dd896c25d44:dXMtY2VudHJhbDEuZ2NwLmNsb3VkLmVzLmlvJDEwNGY0NzdmMzJjNTQ3MmU4NDY5NmVlYTMwZDI0YzMzJDk2NTU5M2I5NGUxZDRhMjU5MDVlMTc5MmY0YzczZGI4",
            basic_auth=("elastic", "eUqFwSxXebwNHSEH1Bjq1zbM"))
        relevant_docs = retrieve_documents_bac(es, "general_texts", 20, speciality)
        context = extract_relevant_context_bac(relevant_docs)
        solutions = generate_solutions_bac(chat, context, num_items)
        solutions_as_items = [{'question': solution, 'choices': None} for solution in solutions.split('\n\n')]

        # Incrementa el contador de preguntas para el usuario actual
        current_user.increment_questions()
        db.session.commit()  # Asegúrate de guardar los cambios en la base de datos

        return render_template('solutions.html', solutions=solutions_as_items)




# Ruta para manejar las solicitudes del chat
@app.route('/chat', methods=['POST'])
def chat():
    user_message = request.json['message']
    chat = ChatDeepInfra(model="meta-llama/Meta-Llama-3-8B-Instruct", max_tokens=4000)
    system_text = "Eres un asistente de examen que proporciona respuestas generales a preguntas relacionadas con el examen."
    human_text = user_message
    prompt = ChatPromptTemplate.from_messages([("system", system_text), ("human", human_text)])
    
    response = prompt | chat
    response_msg = response.invoke({})
    response_text = response_msg.content
    
    return jsonify({"response": response_text})


@app.route('/check', methods=['POST'])
def check():
    data = request.get_json()
    print("Datos recibidos del frontend:", data)  # Imprimir los datos recibidos

    if not data:
        print("Error: No se recibieron datos.")
        return jsonify({"error": "No se recibieron datos"}), 400
    
    questions = data.get('questions')
    user_answers = data.get('answers')

    if not questions or not user_answers:
        print("Error: Faltan preguntas o respuestas.")
        return jsonify({"error": "Faltan preguntas o respuestas"}), 400

    chat = ChatDeepInfra(model="meta-llama/Meta-Llama-3-8B-Instruct", max_tokens=4000)
    results = []

    for i, question in enumerate(questions):
        question_name = f'question_{i+1}'
        user_answer = user_answers.get(question_name)
        
        print(f"Procesando {question_name}: respuesta seleccionada = {user_answer}")  # Imprimir respuesta seleccionada
        
        if not user_answer:
            print(f"{question_name} sin respuesta seleccionada.")
            results.append({
                'question': question,
                'selected_option': None,
                'correct': "incorrect",
                'explanation': "No se proporcionó ninguna respuesta"
            })
            continue

        correctness, explanation = check_answer_exani(question, user_answer, chat)
        print(f"Resultado de {question_name}: correcto = {correctness}, explicación = {explanation}")  # Imprimir resultados

        results.append({
            'question': question,
            'selected_option': user_answer,
            'correct': correctness,
            'explanation': explanation
        })

    return jsonify(results)



# @app.route('/')
# def index():
#   return render_template('index.html')

@app.route('/checkout')
def checkout():
    return render_template('checkout.html')


@app.route('/payment')
def payment():
    return render_template('payment.html')

@app.route('/create-checkout-session', methods=['POST'])
def create_checkout_session():
    try:
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'usd',
                    'product_data': {
                        'name': 'Test Product',
                    },
                    'unit_amount': 2000,  # Amount in cents
                },
                'quantity': 1,
            }],
            mode='payment',
            success_url=url_for('success', _external=True),
            cancel_url=url_for('cancel', _external=True),
        )
        return jsonify(id=checkout_session.id)
    except Exception as e:
        return jsonify(error=str(e)), 400


# Webhook route to handle Stripe events
import stripe

@app.route('/webhook', methods=['POST'])
def webhook():
    payload = request.get_data(as_text=True)
    sig_header = request.headers.get('Stripe-Signature')
    endpoint_secret = 'whsec_iEQcZb38URJgh3gLtkmkWnRWm2BMA72e'  # Asegúrate de que esta sea la clave secreta correcta

    print("Payload recibido:", payload)  # Imprimir el payload recibido
    print("Cabecera de firma recibida:", sig_header)  # Imprimir la cabecera de firma recibida

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, endpoint_secret
        )
        print("Firma validada exitosamente.")
    except ValueError as e:
        # Payload inválido
        print("Error: Payload inválido:", e)
        return '', 400
    except stripe.error.SignatureVerificationError as e:
        # Firma inválida
        print("Error de verificación de firma:", e)
        print("Cabecera de firma esperada:", endpoint_secret)  # Imprimir la clave de firma esperada para comparación
        return '', 400

    # Manejar el evento (por ejemplo, un pago completado)
    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']
        handle_checkout_session(session)

    return '', 200


@app.route('/charge', methods=['POST'])
def charge():
    # `stripeToken` is obtained from the form submission
    token = request.form['stripeToken']

    try:
        # Use Stripe's library to make requests...
        charge = stripe.Charge.create(
            amount=2000,  # $20.00
            currency='usd',
            description='Example charge',
            source=token,
        )
        return render_template('success.html', amount=20)
    except stripe.error.StripeError as e:
        # Handle error
        return str(e)



if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)


