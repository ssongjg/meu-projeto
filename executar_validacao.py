"""
Script para EXECUTAR validacao_artigo_D100.py e capturar resultados
"""

import subprocess
import sys

print("\n" + "="*90)
print(" EXECUTANDO SCRIPT DE VALIDAÇÃO")
print("="*90 + "\n")

# Executar o script
try:
    resultado = subprocess.run(
        [sys.executable, 'validacao_artigo_D100.py'],
        capture_output=True,
        text=True,
        timeout=30
    )
    
    # Exibir output
    print(resultado.stdout)
    
    # Se houver erros
    if resultado.stderr:
        print("\n⚠️ ERROS CAPTURADOS:")
        print(resultado.stderr)
    
    # Código de retorno
    if resultado.returncode == 0:
        print("\n✅ Script executado com sucesso!")
    else:
        print(f"\n❌ Script retornou código de erro: {resultado.returncode}")
        
except subprocess.TimeoutExpired:
    print("❌ Script excedeu o tempo limite de execução")
except FileNotFoundError:
    print("❌ Script 'validacao_artigo_D100.py' não encontrado")
except Exception as e:
    print(f"❌ Erro ao executar: {e}")
