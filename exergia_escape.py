"""
Módulo para cálculo de exergia do escape de motores diesel (D100, D20, D10).
Implementa conversão de composição molar seca→úmida, cálculo de ponto de orvalho,
cp da mistura via polinômios NASA, e exergia sensível + latente com condensação.
"""

import numpy as np
from scipy.integrate import quad
from scipy.optimize import fsolve
import warnings

# Tentar importar CoolProp; caso contrário, usar correlações analíticas
try:
    from CoolProp.CoolProp import PropsSI
    COOLPROP_AVAILABLE = True
except ImportError:
    COOLPROP_AVAILABLE = False
    warnings.warn("CoolProp não disponível. Usando correlações de Antoine.")


# ============================================================================
# CORRELAÇÕES DE ANTOINE PARA PRESSÃO DE SATURAÇÃO DA ÁGUA
# ============================================================================

def antoine_pressure_water(T_K):
    """
    Pressão de saturação da água via correlação de Antoine.
    Válida para 273.15 K < T < 373.15 K (0°C - 100°C).
    
    Args:
        T_K: Temperatura em Kelvin
        
    Returns:
        Pressão de saturação em Pa
    """
    T_C = T_K - 273.15
    
    if T_C < 0 or T_C > 100:
        warnings.warn(f"Temperatura {T_C}°C fora do intervalo de validade de Antoine")
    
    # Constantes de Antoine para água (em bar e °C)
    A, B, C = 8.07131, 1730.63, 233.426
    
    # log10(P_bar) = A - B / (C + T_C)
    log_p_bar = A - B / (C + T_C)
    p_bar = 10 ** log_p_bar
    p_pa = p_bar * 1e5
    
    return p_pa


def get_water_saturation_pressure(T_K):
    """
    Retorna pressão de saturação da água usando CoolProp ou Antoine.
    
    Args:
        T_K: Temperatura em Kelvin
        
    Returns:
        Pressão de saturação em Pa
    """
    if COOLPROP_AVAILABLE:
        try:
            return PropsSI('P', 'T', T_K, 'Q', 0, 'Water')
        except:
            return antoine_pressure_water(T_K)
    else:
        return antoine_pressure_water(T_K)


def get_water_enthalpy_vaporization(T_K):
    """
    Retorna entalpia de vaporização da água em kJ/kg.
    
    Args:
        T_K: Temperatura em Kelvin
        
    Returns:
        Entalpia de vaporização em kJ/kg
    """
    if COOLPROP_AVAILABLE:
        try:
            h_vap = PropsSI('H', 'T', T_K, 'Q', 1, 'Water') - \
                    PropsSI('H', 'T', T_K, 'Q', 0, 'Water')
            return h_vap / 1000  # Converter de J/kg para kJ/kg
        except:
            # Correlação de Watson como fallback
            T_C = T_K - 273.15
            h_vap_100 = 2257  # kJ/kg a 100°C
            T_crit = 647.096  # K
            exponent = 0.38
            h_vap = h_vap_100 * ((T_crit - T_K) / (T_crit - 373.15)) ** exponent
            return max(h_vap, 0)
    else:
        # Correlação de Watson: h_vap(T) = h_vap(T_ref) * ((T_crit - T)/(T_crit - T_ref))^n
        T_C = T_K - 273.15
        h_vap_100 = 2257  # kJ/kg a 100°C
        T_crit = 647.096  # K
        exponent = 0.38
        h_vap = h_vap_100 * ((T_crit - T_K) / (T_crit - 373.15)) ** exponent
        return max(h_vap, 0)


def get_water_enthalpy_ref(T_K, T_ref=298.15):
    """
    Entalpia específica da água em relação a T_ref (usualmente 298.15 K).
    Usa CoolProp ou correlação.
    
    Args:
        T_K: Temperatura em Kelvin
        T_ref: Temperatura de referência (padrão: 298.15 K)
        
    Returns:
        Diferença de entalpia em kJ/kg
    """
    if COOLPROP_AVAILABLE:
        try:
            h_T = PropsSI('H', 'T', T_K, 'P', 101325, 'Water') / 1000  # J/kg -> kJ/kg
            h_ref = PropsSI('H', 'T', T_ref, 'P', 101325, 'Water') / 1000
            return h_T - h_ref
        except:
            cp_agua = 4.18  # kJ/(kg·K) (aproximação)
            return cp_agua * (T_K - T_ref)
    else:
        # cp da água ~ 4.18 kJ/(kg·K)
        cp_agua = 4.18
        return cp_agua * (T_K - T_ref)


# ============================================================================
# POLINÔMIOS NASA PARA CP (CAPACIDADE TÉRMICA)
# ============================================================================

# Coeficientes NASA para cp(T) = R * (a1 + a2*T + a3*T^2 + a4*T^3 + a5*T^4)
# Válidos em intervalo de temperatura especificado [T_low, T_high]
# Fonte: NASA Glenn Research Center Polynomials

NASA_COEFFICIENTS = {
    'N2': {
        'range': (300, 1000),
        'a': [3.298677, 1.408240e-3, -3.963222e-6, 5.641515e-9, -2.444854e-12]
    },
    'O2': {
        'range': (300, 1000),
        'a': [3.212936, 1.127486e-3, -5.75615e-7, 1.313877e-9, -8.768554e-13]
    },
    'CO2': {
        'range': (300, 1000),
        'a': [2.356773, 8.984596e-3, -7.123562e-6, 2.459565e-9, -1.388105e-13]
    },
    'H2O': {
        'range': (300, 1000),
        'a': [4.19864056, -2.0364341e-3, 6.52040211e-6, -5.48797062e-9, 1.77197367e-12]
    },
    'CO': {
        'range': (300, 1000),
        'a': [3.579533, -6.1013583e-4, 1.2864695e-6, -1.0894786e-9, 2.7362340e-13]
    },
    'CH4': {  # Metano como representante de HC
        'range': (300, 1000),
        'a': [5.149876, -1.3670352e-2, 4.9821585e-5, -4.6783150e-8, 1.4010898e-11]
    }
}

# Constante universal dos gases: R = 8.314 J/(mol·K)
R_UNIVERSAL = 8.314


def cp_molar_nasa(especie, T_K):
    """
    Capacidade térmica molar (cp) via polinômios NASA.
    
    Args:
        especie: Nome da espécie ('N2', 'O2', 'CO2', 'H2O', 'CO', 'CH4')
        T_K: Temperatura em Kelvin
        
    Returns:
        cp molar em J/(mol·K)
    """
    if especie not in NASA_COEFFICIENTS:
        raise ValueError(f"Espécie {especie} não tem coeficientes NASA disponíveis")
    
    coef = NASA_COEFFICIENTS[especie]
    T_range = coef['range']
    a = coef['a']
    
    # Aviso se fora do intervalo
    if T_K < T_range[0] or T_K > T_range[1]:
        warnings.warn(f"Temperatura {T_K} K fora do intervalo NASA para {especie}: {T_range}")
    
    # cp = R * (a1 + a2*T + a3*T^2 + a4*T^3 + a5*T^4)
    cp_r = a[0] + a[1]*T_K + a[2]*T_K**2 + a[3]*T_K**3 + a[4]*T_K**4
    cp = cp_r * R_UNIVERSAL
    
    return cp  # J/(mol·K)


def cp_medio_mistura(comp_molar, T_min_K, T_max_K):
    """
    Capacidade térmica média da mistura (base molar) integrada entre T_min e T_max.
    
    Args:
        comp_molar: Dicionário {espécie: fração_molar}
        T_min_K: Temperatura mínima (K)
        T_max_K: Temperatura máxima (K)
        
    Returns:
        cp médio em J/(mol·K)
    """
    def cp_mix_func(T):
        cp_total = 0
        for esp, y in comp_molar.items():
            try:
                cp_total += y * cp_molar_nasa(esp, T)
            except ValueError:
                # Usar valor padrão se não houver coeficiente
                pass
        return cp_total
    
    # Integrar cp da mistura de T_min a T_max
    integral, _ = quad(cp_mix_func, T_min_K, T_max_K)
    cp_medio = integral / (T_max_K - T_min_K)
    
    return cp_medio  # J/(mol·K)


def cp_medio_mistura_massa(comp_molar, masses_molares, T_min_K, T_max_K):
    """
    Capacidade térmica média da mistura em base MÁSSICA.
    
    Args:
        comp_molar: Dicionário {espécie: fração_molar}
        masses_molares: Dicionário {espécie: massa_molar_kg/mol}
        T_min_K: Temperatura mínima (K)
        T_max_K: Temperatura máxima (K)
        
    Returns:
        cp médio em J/(kg·K)
    """
    # Converter frações molares para frações mássicas
    massa_total = sum(comp_molar.get(esp, 0) * masses_molares.get(esp, 0) 
                      for esp in comp_molar)
    
    if massa_total <= 0:
        raise ValueError("Fração mássica total inválida")
    
    comp_massica = {esp: (comp_molar.get(esp, 0) * masses_molares.get(esp, 0)) / massa_total 
                    for esp in comp_molar}
    
    # cp molar médio da mistura
    cp_molar_medio = cp_medio_mistura(comp_molar, T_min_K, T_max_K)
    
    # Converter para base mássica: cp_massa = cp_molar / M_media
    M_media = massa_total / sum(comp_molar.values()) if sum(comp_molar.values()) > 0 else 0.029
    cp_massa = cp_molar_medio / M_media  # J/(kg·K)
    
    return cp_massa


# ============================================================================
# CONVERSÃO: COMPOSIÇÃO SECA → ÚMIDA
# ============================================================================

# Massas molares [kg/mol]
MASSAS_MOLARES = {
    'N2': 0.028014,
    'O2': 0.031999,
    'CO2': 0.044009,
    'H2O': 0.018015,
    'CO': 0.028010,
    'HC': 0.016043,  # Aproximar HC como CH4
    'CH4': 0.016043,
    'Ar': 0.039948,
}


def converter_seco_para_umido(comp_seca, m_dot_exh, m_dot_fuel, y_H_fuel, umidade_ar=0.01):
    """
    Converte composição molar/mássica em BASE SECA para BASE ÚMIDA.
    
    Calcula a quantidade de H2O a partir do:
    1. Hidrogênio do combustível (queimado para H2O)
    2. Umidade do ar de admissão
    
    Args:
        comp_seca: Dicionário com frações (molares ou mássicas) em base seca
        m_dot_exh: Vazão mássica do escape [kg/s]
        m_dot_fuel: Vazão mássica do combustível [kg/s]
        y_H_fuel: Fração mássica de H no combustível
        umidade_ar: Umidade específica do ar [kg H2O / kg ar seco]
        
    Returns:
        Dicionário com frações molares em base ÚMIDA
    """
    # Quantidade de H2O gerada pela combustão do hidrogênio
    # 2H + 1/2 O2 -> H2O
    # Massa de H no combustível: m_dot_fuel * y_H_fuel
    # Massa de H2O gerada: (m_dot_fuel * y_H_fuel) * (18.015 / 2.016)
    
    m_dot_H_fuel = m_dot_fuel * y_H_fuel
    m_dot_H2O_from_H = m_dot_H_fuel * (MASSAS_MOLARES['H2O'] / (2 * 1.00794e-3))
    
    # Quantidade de H2O do ar
    m_dot_ar_seco = m_dot_exh / (1 + umidade_ar)  # Aproximação
    m_dot_H2O_from_ar = m_dot_ar_seco * umidade_ar
    
    # Total de H2O
    m_dot_H2O_total = m_dot_H2O_from_H + m_dot_H2O_from_ar
    
    # Converter composição seca para molar (assumindo que foi fornecida em molar)
    # Se foi fornecida em mássica, primeira normalizar
    n_total_seco = sum(comp_seca.values())  # Soma das frações (deve ser ~1.0)
    
    # Frações molares em base seca (normalizar)
    comp_seca_norm = {esp: frac / n_total_seco for esp, frac in comp_seca.items()}
    
    # Calcular número de moles do escape seco
    m_escape_seco = m_dot_exh - m_dot_H2O_total
    
    # Número de moles (aproximado) da mistura seca
    # Usar uma massa molar média ponderada
    M_media_seca = sum(comp_seca_norm.get(esp, 0) * MASSAS_MOLARES.get(esp, 0.029) 
                       for esp in comp_seca_norm)
    n_escape_seco = m_escape_seco / M_media_seca
    
    # Número de moles de H2O
    n_H2O = m_dot_H2O_total / MASSAS_MOLARES['H2O']
    
    # Composição molar úmida
    n_total_umida = n_escape_seco + n_H2O
    comp_umida = {}
    
    for esp, frac_seca in comp_seca_norm.items():
        n_esp = frac_seca * n_escape_seco
        comp_umida[esp] = n_esp / n_total_umida
    
    comp_umida['H2O'] = n_H2O / n_total_umida
    
    return comp_umida


# ============================================================================
# CÁLCULO DE PONTO DE ORVALHO E PRESSÃO PARCIAL DE VAPOR
# ============================================================================

def calcular_ponto_orvalho(y_H2O, p_total_Pa):
    """
    Calcula a temperatura de ponto de orvalho (T_dp) para uma mistura gás-vapor.
    
    Args:
        y_H2O: Fração molar de vapor d'água na mistura
        p_total_Pa: Pressão total em Pa
        
    Returns:
        Temperatura de ponto de orvalho em Kelvin
    """
    p_H2O_parcial = y_H2O * p_total_Pa
    
    # Encontrar T onde p_sat(T) = p_H2O_parcial
    def eq(T):
        p_sat = get_water_saturation_pressure(T)
        return p_sat - p_H2O_parcial
    
    # Chute inicial: ~288 K (15°C)
    try:
        T_dp = fsolve(eq, 288.15)[0]
        T_dp = max(T_dp, 273.15)  # Limitar ao ponto de congelamento
        return T_dp
    except:
        # Fallback
        return 273.15


# ============================================================================
# CÁLCULO DE EXERGIA
# ============================================================================

def exergia_escape(T_esc, p_esc, comp_seca, m_dot_exh, m_dot_fuel, y_H_fuel, 
                   umidade_ar=0.01, T0=298.15, p0=101325):
    """
    Calcula a exergia total do escape (sensível + latente) de um motor diesel.
    
    Args:
        T_esc: Temperatura do escape [K]
        p_esc: Pressão total do escape [Pa] (default 101325)
        comp_seca: Dicionário com frações molares em base seca
                   Ex.: {'N2': 0.75, 'O2': 0.12, 'CO2': 0.10, 'CO': 0.001, 'HC': 0.0001}
        m_dot_exh: Vazão mássica total do escape [kg/s]
        m_dot_fuel: Vazão mássica de combustível [kg/s]
        y_H_fuel: Fração mássica de hidrogênio no combustível (ex.: 0.13 para diesel)
        umidade_ar: Umidade específica do ar [kg vapor / kg ar seco] (default 0.01)
        T0: Temperatura ambiente de referência [K] (default 298.15 K)
        p0: Pressão ambiente [Pa] (default 101325 Pa)
        
    Returns:
        Dicionário com:
        - 'W_sens': Exergia sensível [kW]
        - 'W_lat': Exergia latente [kW]
        - 'W_total': Exergia total [kW]
        - 'T_dp': Temperatura de ponto de orvalho [K]
        - 'cp_medio': cp médio da mistura [kJ/(kg·K)]
        - 'comp_umida': Dicionário com frações molares em base úmida
        - 'm_dot_H2O': Vazão mássica de H2O [kg/s]
    """
    
    # Passo 1: Converter composição seca para úmida
    comp_umida = converter_seco_para_umido(comp_seca, m_dot_exh, m_dot_fuel, y_H_fuel, umidade_ar)
    
    y_H2O = comp_umida.get('H2O', 0)
    
    # Passo 2: Calcular temperatura de ponto de orvalho
    T_dp = calcular_ponto_orvalho(y_H2O, p_esc)
    
    # Passo 3: Calcular cp médio da mistura (base mássica) de T0 a T_esc
    cp_medio_J_molK = cp_medio_mistura(comp_umida, T0, T_esc)
    
    # Calcular massa molar média
    M_media = sum(comp_umida.get(esp, 0) * MASSAS_MOLARES.get(esp, 0.029) 
                  for esp in comp_umida)
    
    cp_medio_J_kgK = cp_medio_J_molK / M_media
    cp_medio_kJ_kgK = cp_medio_J_kgK / 1000
    
    # Passo 4: Calcular exergia SENSÍVEL
    # W_sens = m_dot * cp_medio * (T_esc - T0) - m_dot * R_gas * T0 * ln(p_esc/p0)
    # Onde R_gas = R_universal / M_media [J/(kg·K)]
    
    R_gas = R_UNIVERSAL / M_media  # J/(kg·K)
    
    termo_enthalpico = m_dot_exh * cp_medio_J_kgK * (T_esc - T0)
    termo_entropia = m_dot_exh * R_gas * T0 * np.log(p_esc / p0)
    
    W_sens_J = termo_enthalpico - termo_entropia
    W_sens_kW = W_sens_J / 1000
    
    # Passo 5: Calcular exergia LATENTE
    # A exergia latente reflete a possibilidade de condensação da H2O do T_esc até T0
    # e/ou a recuperação de calor dessa condensação.
    
    # Vazão mássica de H2O
    m_dot_H2O = y_H2O * m_dot_exh / (1 - y_H2O) if (1 - y_H2O) > 0 else 0
    # Correção: usar relação correta entre fração molar e mássica
    # y_H2O (molar) = (n_H2O) / (n_total)
    # w_H2O (mássica) = (m_H2O) / (m_total) = (n_H2O * M_H2O) / (n_total * M_media)
    
    w_H2O = y_H2O * MASSAS_MOLARES['H2O'] / M_media
    m_dot_H2O = w_H2O * m_dot_exh
    
    if m_dot_H2O > 0:
        # Entalpia de vaporização a T_esc
        h_vap_T_esc = get_water_enthalpy_vaporization(T_esc)
        
        # Entalpia de vaporização a T0
        h_vap_T0 = get_water_enthalpy_vaporization(T0)
        
        # Diferença de entalpia da água de T_esc até T0 (condensação até 0% UR em T0)
        h_agua_T_esc = get_water_enthalpy_ref(T_esc, T0)
        h_agua_T0 = 0  # Referência
        
        # Exergia latente (máxima disponível por condensação completa)
        # W_lat = m_dot_H2O * h_vap_T0 + m_dot_H2O * cp_agua * (T0 - T_dp)
        # Simplificado: W_lat ≈ m_dot_H2O * h_vap_T0
        
        cp_agua = 4.18  # kJ/(kg·K)
        W_lat_J = m_dot_H2O * h_vap_T0 * 1000  # Converter de kJ para J
        
        # Termo de entropia da condensação (perda de exergia à T0)
        # dS_cond ≈ m_dot_H2O * h_vap_T0 / T0
        entropia_cond = m_dot_H2O * h_vap_T0 * 1000 / T0  # J/K
        termo_exergia_latente = -T0 * entropia_cond
        
        W_lat_J = W_lat_J + termo_exergia_latente
        
        W_lat_kW = W_lat_J / 1000
    else:
        W_lat_kW = 0
        m_dot_H2O = 0
    
    # Passo 6: Exergia total
    W_total_kW = W_sens_kW + W_lat_kW
    
    # Retornar resultados
    return {
        'W_sens': W_sens_kW,
        'W_lat': W_lat_kW,
        'W_total': W_total_kW,
        'T_dp': T_dp,
        'cp_medio': cp_medio_kJ_kgK,
        'comp_umida': comp_umida,
        'm_dot_H2O': m_dot_H2O,
    }


# ============================================================================
# EXEMPLO DE USO
# ============================================================================

if __name__ == "__main__":
    # Parâmetros de exemplo: Motor diesel D100 em operação normal
    
    # Composição do escape em base seca (frações molares)
    comp_seca_exemplo = {
        'N2': 0.7580,
        'O2': 0.0360,
        'CO2': 0.0865,
        'H2O': 0.0,  # Base seca, portanto H2O = 0
        'CO': 0.0050,
        'HC': 0.0145,
        'Ar': 0.0000,
    }
    
    # Parâmetros do motor
    T_esc = 600  # K (327°C)
    p_esc = 101325  # Pa (1 atm, após catalisador)
    m_dot_exh = 0.85  # kg/s
    m_dot_fuel = 0.060  # kg/s (combustível)
    y_H_fuel = 0.13  # Fração mássica de H no diesel
    umidade_ar = 0.01  # kg H2O / kg ar seco
    
    # Referência ambiental
    T0 = 298.15  # K (25°C)
    p0 = 101325  # Pa
    
    # Calcular exergia
    resultado = exergia_escape(
        T_esc=T_esc,
        p_esc=p_esc,
        comp_seca=comp_seca_exemplo,
        m_dot_exh=m_dot_exh,
        m_dot_fuel=m_dot_fuel,
        y_H_fuel=y_H_fuel,
        umidade_ar=umidade_ar,
        T0=T0,
        p0=p0
    )
    
    # Exibir resultados
    print("=" * 70)
    print("CÁLCULO DE EXERGIA DO ESCAPE - MOTOR DIESEL")
    print("=" * 70)
    print(f"\nCondições de Operação:")
    print(f"  Temperatura do escape: {T_esc} K ({T_esc - 273.15:.1f}°C)")
    print(f"  Pressão do escape: {p_esc / 1e5:.2f} bar")
    print(f"  Vazão mássica do escape: {m_dot_exh:.3f} kg/s")
    print(f"  Vazão mássica de combustível: {m_dot_fuel:.4f} kg/s")
    print(f"  Fração de H no combustível: {y_H_fuel:.2%}")
    print(f"  Umidade do ar: {umidade_ar:.4f} kg H2O/kg ar seco")
    
    print(f"\nComposição do Escape (Base Seca):")
    for esp, frac in comp_seca_exemplo.items():
        if frac > 0:
            print(f"  {esp}: {frac:.4f} ({frac*100:.2f}%)")
    
    print(f"\nComposição do Escape (Base Úmida):")
    for esp, frac in resultado['comp_umida'].items():
        if frac > 1e-6:
            print(f"  {esp}: {frac:.4f} ({frac*100:.2f}%)")
    
    print(f"\nResultados de Exergia:")
    print(f"  Exergia Sensível (W_sens): {resultado['W_sens']:.3f} kW")
    print(f"  Exergia Latente (W_lat): {resultado['W_lat']:.3f} kW")
    print(f"  Exergia Total (W_total): {resultado['W_total']:.3f} kW")
    
    print(f"\nPropriedades Termodinâmicas:")
    print(f"  Temperatura de Ponto de Orvalho: {resultado['T_dp']:.2f} K ({resultado['T_dp'] - 273.15:.1f}°C)")
    print(f"  Capacidade Térmica Média: {resultado['cp_medio']:.4f} kJ/(kg·K)")
    print(f"  Vazão Mássica de H2O: {resultado['m_dot_H2O']:.4f} kg/s")
    
    print("\n" + "=" * 70)
