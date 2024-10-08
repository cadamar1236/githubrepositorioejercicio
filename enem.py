from flask import render_template, request, jsonify
import os
import re
import random
import logging
from elasticsearch import Elasticsearch
from langchain.prompts import ChatPromptTemplate
from langchain_community.chat_models import ChatDeepInfra

# Configuración de logging
logging.basicConfig(level=logging.INFO)

# Configurar el índice en Elasticsearch
INDEX_NAME = "general_texts_enempdfs"

# Token de API
os.environ["DEEPINFRA_API_TOKEN"] = "gtnKXw1ytDsD7DmCSsv2vwdXSW7IBJ5H"

def extract_relevant_context(documents, max_length=500):
    intro_end_patterns = [
        r"Ejercicio [\d]+",  # Captura los títulos de los ejercicios
        r"instrucciones:",
        r"resolver los siguientes problemas:",
        r"resolver CUATRO de los ocho ejercicios",
        r"cada ejercicio completo puntuará"
    ]
    intro_end_regex = '|'.join(intro_end_patterns)
    keywords = ["calcular", "determinar", "resolver", "analizar", "discutir", "si"]
    question_patterns = [r"\b\d+\)", r"\b\d+\.", r"\b\d+\-\)"]

    relevant_text = []
    for doc in documents:
        content = doc['page_content']
        intro_end_match = re.search(intro_end_regex, content)
        if intro_end_match:
            content = content[intro_end_match.start():]  # Incluir desde el inicio del ejercicio

        sentences = content.split('.')
        for sentence in sentences:
            if any(keyword in sentence.lower() for keyword in keywords) or any(re.search(pattern, sentence) for pattern in question_patterns):
                relevant_text.append(sentence.strip())
                if len('. '.join(relevant_text)) >= max_length:
                    return '. '.join(relevant_text)[:max_length]
    return '. '.join(relevant_text)[:max_length]

import re

def process_questions(response_text):
    questions = []
    
    # Dividir el texto en bloques de preguntas basándose en un patrón de "Questão"
    question_blocks = re.split(r"(?=Questão \d+)", response_text.strip())
    
    for block in question_blocks:
        if not block.strip():
            continue
        
        # Extraer el texto de la pregunta
        question_match = re.search(r"Questão \d+\s*(.*?)(?=\n[A-E]\)|\Z)", block, re.DOTALL)
        
        if question_match:
            question_text = question_match.group(1).strip()
            
            # Buscar las opciones en el formato específico
            options = re.findall(r"([A-E])\)\s*(.+?)(?=\n[A-E]\)|\Z)", block, re.DOTALL)
            
            if options:
                # Crear una lista con las opciones
                choices = [option[1].strip() for option in options]
                
                # Añadir la pregunta y sus opciones a la lista de preguntas
                questions.append({'question': question_text, 'choices': choices})
    print("esto es")
    print(questions)
    return questions




def count_words(text):
    words = text.split()
    return len(words)

import re

import re

import re

def generate_questions(chat, pdf_content, num_questions):
    # Escapar llaves y caracteres especiales de LaTeX para evitar interpretaciones erróneas
    escaped_pdf_content = pdf_content.replace("{", "{{").replace("}", "}}")
    escaped_pdf_content = escaped_pdf_content.replace("\\", "\\\\")  # Escapar backslashes para LaTeX

    # Uso de triple comillas y placeholders para evitar conflictos
    system_text = f"""Eres un asistente en portugués (brasil) que genera preguntas de opción múltiple. 
    En caso de términos matemáticos, ponlos en formato LATEX y usa delimitadores LaTeX para matemáticas en línea `\\(...\\)`. 
    Quiero que me generes preguntas con una estructura y contenido similar a las preguntas proporcionadas en el siguiente contexto: {{pdf_content}}. 
    Pon solo las preguntas y respuestas, NO HAGAS comentarios, NO PONGAS la respuesta correcta en las opciones. 
    Coge la estructura, incluyendo en la pregunta inicial TODO el texto para formular la pregunta y las posibles opciones, como en el siguiente formato:

    Questão 95: No programa do balé Parade, apresentado em...
    A) a falta de diversidade cultural na sua proposta estética.
    B) a alienação dos artistas em relação às tensões da Segunda Guerra Mundial.
    C) uma disputa cênica entre as linguagens das artes visuais, do figurino e da música.
    D) as inovações tecnológicas nas partes cênicas, musicais, coreográficas e de figurino.
    E) uma narrativa com encadeamentos claramente lógicos e lineares.

    Por favor, genera {{num_questions}} preguntas, asegurándote de incluir suficiente contexto en cada enunciado."""
    
    human_text = "Genera preguntas con la estructura descrita a partir del contenido del PDF:"
    
    input_text = system_text + "\n" + human_text
    input_word_count = count_words(input_text)
    print(f"Number of words in the input: {input_word_count}")

    prompt = ChatPromptTemplate.from_messages([
        ("system", system_text),
        ("human", human_text + "\n" + escaped_pdf_content)
    ])
    
    # Pasar los datos como parte de las variables de la plantilla para evitar conflictos
    prompt_input = {
        "pdf_content": escaped_pdf_content,
        "num_questions": num_questions,
    }
    
    response = prompt | chat
    response_msg = response.invoke(prompt_input)
    response_text = response_msg.content
    print(f"Prompt: {response_text}")

    questions = process_questions(response_text)
    return questions



import re
from langchain.prompts import ChatPromptTemplate
from langchain_community.chat_models import ChatDeepInfra
from langchain.schema import HumanMessage, SystemMessage 

import re
from langchain_community.chat_models import ChatDeepInfra
from langchain.schema import HumanMessage, SystemMessage

def check_answer(question, user_answer, chat):
    try:
        # Preparar el contenido de la pregunta y las opciones
        print("hola")
        question_text = question["question"]
        options = "\n".join([f"{chr(65 + i)}. {choice}" for i, choice in enumerate(question["choices"])])
        
        # Primer mensaje para obtener la respuesta correcta
        system_message = SystemMessage(content="Você é um assistente que avalia perguntas de múltipla escolha. Dada a pergunta e as opções, determine a resposta correta. Sua resposta deve começar com a letra da opção correta (A, B, C, D ou E) seguida por uma explicação breve.")
        human_message = HumanMessage(content=f"Pergunta: {question_text}\n\nOpções:\n{options}")
        
        response = chat([system_message, human_message])
        response_text = response.content

        # Extraer la respuesta correcta
        match = re.match(r"^(A|B|C|D|E)", response_text.strip(), re.IGNORECASE)
        if match:
            correct_answer = match.group(1).upper()
        else:
            raise ValueError("Não foi possível determinar a resposta correta a partir do modelo.")

        # Segundo mensaje para obtener la explicación
        system_explanation = SystemMessage(content="Você é um assistente que fornece uma explicação detalhada de por que uma resposta está correta ou incorreta.")
        human_explanation = HumanMessage(content=f"Pergunta: {question_text}\nResposta correta: {correct_answer}")
        
        response_explanation = chat([system_explanation, human_explanation])
        explanation = response_explanation.content.strip()

        # Comparar la respuesta del usuario con la respuesta correcta
        if user_answer.upper() == correct_answer:
            return "correct", f"Sim, a resposta está correta. A resposta correta é '{correct_answer}'.\nExplicação: {explanation}"
        else:
            return "incorrect", f"Não, a resposta está incorreta. A resposta correta é '{correct_answer}', não '{user_answer}'.\nExplicação: {explanation}"

    except Exception as e:
        print(f"Erro em check_answer: {e}")
        return "error", f"Erro ao avaliar a resposta: {e}"







def retrieve_documents(es, index_name, num_docs=20, cuaderno_seleccionado=None):
    search_query = {
        "query": {
            "bool": {
                "must": [
                    {"match_all": {}}
                ],
                "filter": [
                    {"wildcard": {"metadata.source": f"*{cuaderno_seleccionado}*"}}
                ] if cuaderno_seleccionado else []
            }
        },
        "size": num_docs * 2  # Recuperar más documentos para asegurar suficientes después del filtrado
    }

    print(f"search_query: {search_query}")  # Debug
    response = es.search(index=index_name, body=search_query)
    documents = [
        {
            "page_content": hit["_source"]["content"],
            "metadata": hit["_source"]["metadata"]
        }
        for hit in response["hits"]["hits"]
    ]
    print(f"Retrieved {len(documents)} documents")  # Debug
    for doc in documents:
        print(f"Documento: {doc['metadata']}")

    # Filtrar los documentos de las primeras 10 páginas
    filtered_documents = [
        doc for doc in documents if int(doc['metadata'].get('page', 0)) > 10
    ]
    print(f"Filtered documents count: {len(filtered_documents)}")
    for doc in filtered_documents:
        print(f"Documento: {doc['metadata']}")

    # Aleatorizar los documentos
    random.shuffle(filtered_documents)

    # Limitar el número de documentos a devolver
    return filtered_documents[:num_docs]

