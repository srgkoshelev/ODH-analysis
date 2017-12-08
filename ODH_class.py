#!python3
#Defining base classes for ODH analysis

import math, sys, logging
import matplotlib.pyplot as plt
sys.path.append('D:/Personal/Python repo/')
from heat_transfer import functions as ht
from heat_transfer import piping as pipe

logging.basicConfig(format='%(levelname)s:%(message)s', level=logging.DEBUG)
logger = logging.getLogger(__name__)

#Setting up the units
ureg = ht.ureg
Q_ = ureg.Quantity
#ureg.auto_reduce_dimensions = True

#Setting units for "standard" flow
T_NTP = Q_(68, ureg.degF) #Normal Temperature (NIST)
P_NTP = Q_(14.7, ureg.psi) #Normal Pressure (NIST)

T_MSC = Q_(15, ureg.degC) #Metric Standard Conditions (used by Crane TP-410)
P_MSC = Q_(101325, ureg.Pa) #Metric Standard Conditions (used by Crane TP-410)

#Probability of failure on demand for main cases
PFD_power = 1e-4 #For some reason FESHM chapter lists 3e-4 as demand rate and 1e-4/hr failure rate + 1 hr time off. D. Smith's Reliability... states that PFD = lambda*MTD with lambda = 1e-4 1/hr and MDT = 1 hr gives 1e-4 value.
PFD_odh = 2e-3 #Conservative etimate; form CMTF Hi Bay ODH EN01878 pp. 27-28. That is the most correct value I have seen in use
System_fail_prob = [PFD_power, PFD_odh] #list of probabilities that may cause fan not to run (fan failures are calculated separately)

class odh_source:
    """Define the possible source of inert gas"""
    instances = [] #Keeping information on all the sources within the class
    def __init__ (self, name, fluid, Volume, phase = 'gas', pressure = P_NTP): 
        odh_source.instances.append(self) #adding initialized instance to instances list
        self.name = name
        self.fluid = fluid
        self.Leaks = {}
        assert phase in ['vapor','liquid', 'gas'], 'Phase can only be liquid or vapor: %r' % phase
        if phase == 'vapor' or phase == 'gas':
            self.volume = Volume*pressure/Q_(14.7,ureg.psi)
        elif phase == 'liquid':
            Fluid_data = ht.pack_fluid(fluid, T_NTP, P_NTP) #standard conditions
            (x, M, D_fluid_std) = ht.rp_init(Fluid_data)
            satur = ht.satp(101325*ureg.Pa, x)
            #D_fluid_sat = Q_(satur ['Dliq'], ureg('mol/L'))
            D_fluid_sat = satur ['Dliq']*ureg('mol/L')
            self.volume = Volume*D_fluid_sat/D_fluid_std
        self.volume.ito(ureg('ft**3'))

    def __add__ (self, other):
        if self.fluid == other.fluid:
            total_volume = self.volume + other.volume
            return odh_source(None, self.fluid, total_volume, 'gas', P_NTP) 
        else:
            raise Exception ('\nBoth volumes should contain the same fluid')

    def __str__ (self):
        if self.name:
            return self.name+' is an ODH source of ' + self.fluid + ' with volume = ' + '{:.3~}'.format(self.volume)
        else:
            return ''



    def calculate_gas_leak (self, cause, failure_mode):
        '''
        Helper function for different cases of gas pipe/weld leaks and ruptures
        '''
        Pipe = failure_mode['Pipe']
        N_welds = failure_mode.get('N_welds')
        Area = None
        if cause == 'small leak':
            cause = 'small leak ' + str(pipe.D_pipe(Pipe))
            Prob = 10**(-9)/(ureg.m*ureg.hr)*Pipe['L'] 
            Area = 10*ureg.mm**2
        elif cause == 'large leak':
            if pipe.D_pipe(Pipe) > 2:
                cause = 'large leak ' + str(pipe.D_pipe(Pipe))
                Prob = 10**(-10)/(ureg.m*ureg.hr)*Pipe['L'] 
                Area = 1000*ureg.mm**2
        elif cause == 'rupture':
            cause = 'rupture ' + str(pipe.D_pipe(Pipe))
            Prob = 3*10**(-11)/(ureg.m*ureg.hr)*Pipe['L'] 
            Area = pipe.Area(Pipe)
        elif cause == 'weld small leak':
            cause = 'weld small leak ' + str(pipe.D_pipe(Pipe))
            Prob = N_welds*2*10**(-11)/(ureg.hr)*pipe.OD(Pipe)/pipe.wall(Pipe) 
            Area = 10*ureg.mm**2
        elif cause == 'weld large leak':
            if pipe.D_pipe(Pipe) > 2:
                cause = 'weld large leak ' + str(pipe.D_pipe(Pipe))
                N_welds = failure_mode['N_welds']
                Prob = N_welds*2*10**(-12)/(ureg.hr)*pipe.OD(Pipe)/pipe.wall(Pipe)
                Area = 1000*ureg.mm**2
        elif cause == 'weld rupture':
            cause = 'weld rupture ' + str(pipe.D_pipe(Pipe))
            Prob = N_welds*6*10**(-13)/(ureg.hr)*pipe.OD(Pipe)/pipe.wall(Pipe) 
            Area = pipe.Area(Pipe)
        else: 
            logger.warning ('Gas pipe failure cause is not recognized!:{}'.format(cause))

        if Area:
            q_std = limit_flow(leak_flow(failure_mode['Fluid_data'],  Area), failure_mode)
            tau = self.volume/q_std
            Prob.ito(1/ureg.hr)
            self.Leaks[cause] = (Prob, q_std, tau.to(ureg.min))


    def leak (self, failure_mode = {'mode': 'gas line', 'Pipe':{'L':10*ureg.m, 'D_nom':3*ureg.inch, 'SCH':5}, 'Fluid_data': {'P':2.33*ureg('bar'), 'T':Q_(40,ureg.degC)}, 'max_flow':0.01*ureg('m^3/s')}):
        '''Calculating leak and probability for different cases of equipment failure
        '''
        Fluid_data = failure_mode.get('Fluid_data')
        if Fluid_data:
            (fluid, T_fluid, P_fluid) = ht.unpack_fluid(failure_mode.get('Fluid_data'))
            fluid = self.fluid #is defined by the source
            Fluid_data = ht.pack_fluid(fluid, T_fluid, P_fluid)
        else:
            Fluid_data = {'fluid':self.fluid}
        failure_mode['Fluid_data'] = Fluid_data
        Pipe = failure_mode.get('Pipe')

        if failure_mode['mode'] == 'gas line':
            Causes = ['small leak', 'large leak', 'rupture', 'weld small leak', 'weld large leak', 'weld rupture']
            for cause in Causes:
                self.calculate_gas_leak(cause, failure_mode)

        elif failure_mode['mode'] == 'fluid line':
            cause = 'fluid line leak'
            N_lines = failure_mode['N_lines']
            Prob = N_lines*5*10**(-7)/(ureg.hr) #Probaility and flow will be recalculated for each cause using the same variable names
            Prob.ito(1/ureg.hr)
            q_std = limit_flow(leak_flow(Fluid_data,  10*ureg.mm**2), failure_mode)
            tau = self.volume/q_std
            self.Leaks[cause] = (Prob, q_std, tau.to(ureg.min))

            cause = 'fluid line rupture'
            Prob = N_lines*5*10**(-8)/(ureg.hr) #FESH chapter uses unconservative approach with ~60% confidence; This value gives 95% confidence and is widely used: see rule of 3
            Prob.ito(1/ureg.hr)
            q_std = limit_flow(leak_flow(Fluid_data,  pipe.Area(Pipe)), failure_mode)
            tau = self.volume/q_std
            self.Leaks[cause] = (Prob, q_std, tau.to(ureg.min))

        elif failure_mode['mode'] == 'dewar':
            cause = 'loss of vacuum to air'
            Prob = 1*10**(-6)/(ureg.hr) #Probaility and flow will be recalculated for each cause using the same variable names
            Prob.ito(1/ureg.hr)
            q_std = limit_flow(failure_mode['q_relief'], failure_mode)
            tau = self.volume/q_std
            self.Leaks[cause] = (Prob, q_std, tau.to(ureg.min))

        elif failure_mode['mode'] == 'other':
            cause = failure_mode['cause']
            Prob = failure_mode['Prob'] #Probability of a single failure
            if Prob == 1:
                Prob_total = 1
            else:
                Prob_total = Prob*failure_mode.get('N', 1) #Total probability of failure
                Prob.ito(1/ureg.hr)
            q = limit_flow(failure_mode['q'], failure_mode)
            q_std = to_standard_flow(q, Fluid_data)
            tau = self.volume/q_std
            self.Leaks[cause] = (Prob, q_std, tau.to(ureg.min))

        else:
            raise  ValueError ('Mode is not supported: %r. Try "gas line", "fluid line", "dewar" or "other".', failure_mode['mode'])

        return self.Leaks

    def print_leaks (self):
        for key in sorted(self.Leaks.keys()):
            print ('Failure mode: ' + key + ' pipe')
            print ('Failure rate: {:.2~}'.format(self.Leaks[key][0]))
            print ('Flow rate: {:.2~}'.format(self.Leaks[key][1].to(ureg.ft**3/ureg.min)))
            print ('Event duration: {:.2~}'.format(self.Leaks[key][2]))
            print ()


    

class odh_volume:
    '''
    Volume/building affected by inert gases.
    '''
    def __init__  (self, Fluids, volume):
        self.Fluids = Fluids
        self.volume = volume

    def fan_fail (self, Test_period, Mean_repair_time, Fail_rate, Q_fan, N_fans):
        '''
        Calculate (Probability, flow) pairs for all combinations of fans working. All fans are expected to have same volume flow
        '''
        Fan_flowrates = []
        PFD_prev = 1
        for m in range(N_fans, -1, -1):
            PFD_fan = failure_on_demand(m, N_fans, Test_period, Mean_repair_time, Fail_rate) #Probability of failure if m units is required; Equivalent to 0..m units working
            PFD_m_fan_work = PFD_prev - PFD_fan #Probability exactly m fans work = P(0..m+1) - P(0..m) = PFD_prev-PFD_fan
            Fan_flowrates.insert(0, (PFD_m_fan_work, Q_fan*m))
            PFD_prev = PFD_fan
        self.Fan_flowrates = Fan_flowrates

    def PFD_system (self, System_fail_prob):
        '''
        Calculate total probability of an ODH system failure for given probabilities of different element failures.
        In most cases those probabilities are not mutually exclusive and cannot be added directly. Fortunately, the non-failure events are indepent
        '''
        PFD_system_on = 1
        for PFD in System_fail_prob:
            PFD_system_on *= 1-PFD 
        self.PFD_system = 1-PFD_system_on 

    def fatality_prob(self, O2_conc):
        if O2_conc >= 0.18: #Lowest oxygen concentration above 18%
            Fi = 0
        elif O2_conc <= 0.088: #8.8% of oxygen is assumed to be 100% fatal
            Fi = 1
        else:
            Fi = 10**(6.6956-76.087*O2_conc) #Fi formula, reverse engineered using 8.8% and 18% thresholds; These values are used in FESHM chapter to approximate O2 partial pressure
        return Fi

    def odh_class(self):
        if self.phi < 1e-7/ureg.hr:
            return 0
        elif self.phi < 1e-5/ureg.hr:
            return 1
        elif self.phi < 1e-3/ureg.hr:
            return 2
        else:
            raise Exception ('ODH fatality rate is too high. Please, check calculations')


    def odh (self, Sources):
        '''
        Calculate ODH fatality rate and recommend ODH class designation
        '''
        self.phi = 0 #fatality rate
        for source in Sources: #Sources is a list of odh_source objects
            if source.fluid in self.Fluids:
                for key in source.Leaks.keys():
                    (P_leak, q_leak, tau) = source.Leaks[key]
                    P_i = P_leak*self.PFD_system #power or ODH system failure
                    O2_conc = conc_vent (self.volume, q_leak, 0*ureg('ft^3/min'), tau) #is limited by ammount of inert gas the source has; fans are not operational
                    F_i = self.fatality_prob(O2_conc)
                    if P_i*F_i >= 1e-9/ureg.hr:
                        logger.info ('Source: '+source.name)
                        logger.info ('Failure: '+key+' and No power')
                        logger.info ('Oxygen, percent of norm: {:.2%}'.format(O2_conc/0.21))
                        logger.info ('Leak rate: {:.2~}'.format(P_leak))
                        logger.info ('System prob: {:.2%}'.format(self.PFD_system))
                        logger.info ('Failure rate: {:.2~}'.format(P_i))
                        logger.info ('Fatality prob: {:.2g}'.format(F_i))
                        logger.info ('Fatality rate: {:.2~}\n'.format(P_i*F_i))
                    self.phi += P_i*F_i
                    if hasattr(self, 'Fan_flowrates'):
                        if hasattr(self, 'PFD_system'):
                            for (P_fan, Q_fan) in self.Fan_flowrates:
                                P_i = P_leak*(1-self.PFD_system)*P_fan #Probability of leak occuring, ODH system/power working and m number of fans working
                                O2_conc = conc_vent (self.volume, q_leak, Q_fan, tau)
                                #O2_conc = conc_final (self.volume, q_leak, Q_fan)
                                F_i = self.fatality_prob(O2_conc)
                                if P_i*F_i >= 1e-9/ureg.hr:
                                    logger.info ('Source: '+source.name)
                                    logger.info ('Failure: '+key)
                                    logger.info ('Oxygen, percent of norm: {:.2%}'.format(O2_conc/0.21))
                                    logger.info ('Leak rate: {:.2~}'.format(P_leak))
                                    logger.info ('System prob: {:.2%}'.format(1-self.PFD_system))
                                    logger.info ('Failure rate: {:.2~}'.format(P_i))
                                    logger.info ('Leak rate: {:.2~}'.format(q_leak))
                                    logger.info ('Fan rate: {:.2~}'.format(Q_fan))
                                    logger.info ('Fatality prob: {:.2g}'.format(F_i))
                                    logger.info ('Fatality rate: {:.2~}\n'.format(P_i*F_i))
                                self.phi += P_i*F_i
                        else:
                            raise Exception ('Need to calculate ODH system failure probability first')
                    else:
                        raise Exception ('Need to calculate Fan flowrates first')
        print ('Fatality rate for this volume is {:.3}'.format(self.phi))

        print ('Recommended ODH class {}'.format(self.odh_class()))
        



def limit_flow(flow, failure_mode):
    '''
    Leak through the openning sometimes cannot be larger than a certain number, e.g. a compressor throughput. This function limits flow to this value if it exists
    '''
    max_flow = failure_mode.get('max_flow')
    if max_flow:
        max_flow = to_standard_flow(max_flow, failure_mode['Fluid_data']) #converting max_flow to standard volumetric flow  
        return min (flow, max_flow)
    else:
        return flow

def failure_on_demand (m, n, Test_period, Mean_repair_time, failure_rate):
    '''
    Failure on demand probability
    The definition in FESHM chapter (probability of m units out of n starting) is incorrect. Definition in D. Smoth's Reliability... is confusing.
    This value represents the probability of system failure if for normal functioning only m units out of n is required.
    1 - PFD gives the probability of at least m units out of n starting. That is usually not what required for ODH analysis. The workaround should be used.
    '''
    MDT = Test_period/2+Mean_repair_time
    PFD = ((MDT*failure_rate*m)**(n+1-m))/math.factorial(n+1-m)
    return PFD



def to_standard_flow(flow_rate, Fluid_data):
    '''
    Converting volumetric flow at certain conditions or mass flow to volumetric flow at NTP
    '''
    (x, M, D_NTP) = ht.rp_init({'fluid':Fluid_data['fluid'], 'T':T_NTP, 'P':P_NTP})
    if flow_rate.dimensionality == ureg('kg/s').dimensionality: #mass flow, flow conditions are unnecessary
        q_std = flow_rate/(D_NTP*M)
    elif flow_rate.dimensionality == ureg('m^3/s').dimensionality: #volumetric flow given, converting to standard pressure and temperature
        (fluid, T_fluid, P_fluid) = ht.unpack_fluid(Fluid_data)
        (x, M, D_fluid) = ht.rp_init(Fluid_data)
        q_std = flow_rate*D_fluid/D_NTP
    q_std.ito(ureg.m**3/ureg.s)
    return q_std



def leak_flow (Fluid_data = {'fluid':'air', 'P':2.33*ureg('bar'), 'T':Q_(40,ureg.degC)}, A = 10*ureg('mm**2')):
    d = (4*A/math.pi)**0.5
    Y = 1 #conservative value; from Crane TP-410 A-21
    C = 0.7 #conservative value; from Crane TP-410 A-20 
    (fluid, T_fluid, P_fluid) = ht.unpack_fluid(Fluid_data)
    (x, M, D_fluid) = ht.rp_init(Fluid_data)
    rho = D_fluid*M
    k = ht.gamma(Fluid_data) #adiabatic coefficient
    rc = (2/(k+1))**(k/(k-1)) #Critical pressure drop; Note: according to Crane TP-410 is depndent on the hydraulic resistance of the flow path
    if P_MSC > P_fluid*rc: #subsonic flow
        DeltaP = P_fluid - P_MSC
    else: #Sonic flow
        DeltaP = P_fluid*(1-rc) #Crane TP-410, p 2-15
    quality = ht.flsh('TP', T_fluid, P_fluid, x)['q']
    if quality >= 1:
        (x_a, M_a) = ht.rp_init({'fluid':'air'})
        S_g = M/M_a #Specific gravity
        q = orifice_flow_gas(Y, d, C, S_g, DeltaP, rho)
    else:
        q = orifice_flow_liquid(d, C, DeltaP, rho) #Calculating liquid flow through the opening. Assuming no evaporation happens

    q_NTP = to_standard_flow(q, Fluid_data) #convert to NTP standard point
    q_NTP.ito(ureg.cubic_feet/ureg.min)
    return q_NTP

def orifice_flow_gas (Y, d, C, S_g, DeltaP, rho):
    '''Wrapper function for flow through orifice equation.
    Original formula in crane uses a non-dimensionless coefficient 0.0002864 and non-SI units for values in formulas so straightforward approach is complicated.
    '''
    d_1 = d.to(ureg.mm).magnitude #diameter in mm
    Deltap = DeltaP.to(ureg.bar).magnitude #Pressure drop in bar
    rho_1 = rho.to(ureg.kg/ureg.m**3).magnitude #Density of inflow in kg/m^3
    q = 0.0002864*Y*d_1**2*C/(S_g)*(Deltap*rho_1)**0.5 #Flow at MSC in m^3/s; Crane TP-410, Eq.3-22
    q = q*ureg('m^3/s')
    return q


def orifice_flow_liquid (d, C, DeltaP, rho):
    '''Wrapper function for flow through orifice equation.
    Original formula in crane uses a non-dimensionless coefficient 0.0002864 and non-SI units for values in formulas so straightforward approach is complicated.
    '''
    d_1 = d.to(ureg.mm).magnitude #diameter in mm
    Deltap = DeltaP.to(ureg.bar).magnitude #Pressure drop in bar
    rho_1 = rho.to(ureg.kg/ureg.m**3).magnitude #Density of inflow in kg/m^3
    q = 0.0003512*d_1**2*C*(Deltap/rho_1)**0.5 #Flow at MSC in m^3/s; Crane TP-410, Eq.3-21
    q = q*ureg('m^3/s')
    return q


def conc_vent (V, R, Q, t):
    #V - volume of the confined space (ft3 or m3)
    #R - spill rate into confined space (scfm or m3/s)
    #Q = ventilation rate of fan(s), (cfm or m3/s); positive value corresponds to blowing air into the confined space, negative - drawing contaminated air outside
    #t = time, (minutes or seconds) beginning of release is at t=0
    #C - oxygen concentration in confined space
    #Case B
    V = V.to(ureg.m**3).magnitude #There seems to be a bug with unit package regarding epxonentiation
    R = R.to(ureg.m**3/ureg.s).magnitude
    Q = Q.to(ureg.m**3/ureg.s).magnitude
    t = t.to(ureg.s).magnitude
    if Q > 0:
        C = 0.21/(Q+R)*(Q+R*math.e**-((Q+R)/V*t))
    elif abs(Q) <= R:
        C = 0.21*math.e**-(R/V*t)
    elif abs(Q) > R:
        C = 0.21*(1-R/abs(Q)*(1-math.e**-(abs(Q)*t/V)))
    return C


def conc_final (V, R, Q):
    #V - volume of the confined space (ft3 or m3)
    #R - spill rate into confined space (scfm or m3/s)
    #Q = ventilation rate of fan(s), (cfm or m3/s); positive value corresponds to blowing air into the confined space, negative - drawing contaminated air outside
    #C - oxygen concentration in confined space
    #Case B
    if Q > 0*ureg('ft^3/min'):
            C = 0.21/(Q+R)*Q
    elif abs(Q) <= abs(R):
            C = 0
    elif abs(Q) > abs(R):
            C = 0.21*(1-R/abs(Q))
    return C

def conc_after (V, C_e, Q, t, t_e):
    #V - volume of the confined space (ft3 or m3)
    #R - spill rate into confined space (scfm or m3/s)
    #Q = ventilation rate of fan(s), (cfm or m3/s); positive value corresponds to blowing air into the confined space, negative - drawing contaminated air outside
    #t = time, (minutes or seconds) beginning of release is at t=0
    #C - oxygen concentration in confined space
    #C_e = oxygen concentration when the release has ended
    C = 0.21-(0.21-C_e)*np.e**-(abs(Q)/V*(t-t_e))
    return C














    
if __name__ == "__main__":
    #Adding the inert gas sources

    He_storage_dewar_gas = odh_source('Storage dewar', 'helium', Q_(33900, ureg.cubic_feet), 'vapor', Q_(0, ureg.psig)) #blowdown from 12 psig to 1 atmosphere, estimated by R. Rabehl, TID-N-3A, p. 7

    He_storage_dewar_gas.leak({'mode':'gas line', 'Pipe':{'D_nom':4, 'SCH':5, 'L':35*ureg.ft}, 'N_welds':10, 'Fluid_data':{'P': 285*ureg.psig, 'T':300*ureg.K}, 'max_flow':211*ureg('g/s')}) #Supply to Cold Box; Max flow is taken as Sullair max capacity at discharge
    He_storage_dewar_gas.leak({'mode':'gas line', 'Pipe':{'D_nom':6, 'SCH':5, 'L':35*ureg.ft}, 'N_welds':10, 'Fluid_data':{'P': 35*ureg.psig, 'T':300*ureg.K}, 'max_flow':108*ureg('g/s')}) #Mid stage; Max flow is taken as midstage return from cryoplant
    He_storage_dewar_gas.leak({'mode':'gas line', 'Pipe':{'D_nom':8, 'SCH':5, 'L':35*ureg.ft}, 'N_welds':10, 'Fluid_data':{'P': 16.5*ureg.psig, 'T':300*ureg.K}, 'max_flow':100*ureg('g/s')}) #Return (suction); Max flow is taken from Mycom room ODH analysis as Total supply rate to suction piping


    #for source in odh_source.instances:
    #    print (source.volume)
    #    print()
    #    source.print_leaks ()
    #    print('\n'*2)
    Test_period = 1*ureg('month') #IB1 fan test period 
    Test_period.ito(ureg.hr)
    Mean_repair_time = 3*ureg('days') #IB1 fan/louver average repair time: most delay is caused by response time, it has recently improved from about 1 week to 1 day. The average value of 3 days is assumed
    l_fan = 9e-6/ureg.hr #Fan failure rate
    l_louv = 3e-7/ureg.hr #Louver failure rate
    l_vent = l_fan+l_louv #At IB1 louver and fan are installed in series; failure of either one results in no venting
    Q_fan = 4000*ureg.ft**3/ureg.min #Flowrate of 4 ceiling fans is >= 4000 CFM; positive value refers to blowing into the building: FALSE IN CASE OF IB1!
    N_fans = 4

    A = Q_(1550, ureg.square_feet) #IB1 floor area
    Coldbox_platform = odh_volume(['helium'], A*3.3*ureg.feet) #Platform above the coldbox, 40 in below ceiling
    IB1_2nd_floor = odh_volume(['helium'], A*9.8*ureg.feet) #Second floor offices, floor above washrooms, north and south power supply mezzanines, stand 4 platform
    IB1_air = odh_volume(['helium', 'nitrogen'], A*19.8*ureg.feet) #All IB1 air
    Ground_floor = odh_volume(['nitrogen'], A*0.6*ureg.m) #Bottom layer of the building, affected by cold nitrogen gas; 0.6m is average height of a sitting person

    IB1_air.fan_fail(Test_period, Mean_repair_time, l_vent, Q_fan, N_fans)
    #print (IB1_air.Fan_flowrates)
    IB1_air.PFD_system (System_fail_prob)
    IB1_air.odh(odh_source.instances)

    tau = list(range(math.ceil(266300*60/7750)))*ureg.s
    Q = 16000*ureg('ft^3/min')
    R = 1.1e4*ureg('ft^3/min')
    V = 15500*19.8*ureg('ft^3')
    C_in = []
    C_out = []
    for t in tau:
        #C_in.append (conc_vent(V, R, Q, t))
        C_out.append (1- conc_vent(V, R, -Q, t)/0.21)

    plt.plot(tau.magnitude, C_out, label = 'Out')
    #plt.plot(tau.magnitude, C_in, label = 'In')
    plt.legend()
    #plt.show()




    #def fan_fail (Test_period, Mean_repair_time, Fail_rate, Q_fan, N_fans):


    #if self.fluid == 'helium':
    #    V_effect = [A*h for h in He_height]
    #elif self.fluid == 'nitrogen':
    #    V_effect = [A*N2_height] #Volume affected by inert gas

    #def safe (self, escape = True): #move into calculation part - it's a single case things
    #    A = Q_(1550, ureg.square_feet) #IB1 floor area
    #    He_height = [3.3, 9.8, 19.8]*ureg.feet
    #    N2_height = 0.6*ureg.m #height of a sitting person
    #    if self.fluid == 'helium':
    #        V_effect = [A*h for h in He_height]
    #    elif self.fluid == 'nitrogen':
    #        V_effect = [A*N2_height] #Volume affected by inert gas
    #    if escape == True: #if mixed air is allowed to escape within considered volume
    #        O2_conc = [0.21*V/(V+self.volume) for V in V_effect]
    #    else: #worst case; inert gas is trapped and expells the air outside the considered volume
    #        O2_conc = [0.21*(1-self.volume/V) for V in V_effect]
    #    #print ( [self.fatality_prob(conc) == 0 for conc in O2_conc])
    #    return all([self.Q_fafatality_probn(conc) == 0 for conc in O2_conc])

