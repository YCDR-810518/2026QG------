import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
import json

class control_system:
    def __init__(self, n, X_init, V_init, xL, vL, R,
                A, K, beta=1.0, gamma=1.0, dt=0.01):
        '''
        n 车辆数量
        X_init, V_init 初始位置与速度
        xL, vL Leader位置与速度
        R 期望纵向与横向间距
        A,K 邻接与相连权重矩阵
        beta, gamma 参数
        dt 时间微分
        '''
        self.n = n 

        # 转换成浮点数后赋值
        self.X = X_init.astype(float)   
        self.V = V_init.astype(float)
        self.xL = xL.astype(float)
        self.vL = vL.astype(float)
        self.R = R.astype(float)
        self.A = A.astype(float)
        self.K = K.astype(float)

        self.beta = beta
        self.gamma = gamma
        self.dt = dt

        # x、y方向上位置与速度收敛时间
        self.tx = None
        self.ty = None
        self.tvx = None
        self.tvy = None

        # 度矩阵
        D = np.diag(np.sum(self.A, axis = 1))
        # 拉普拉斯矩阵
        self.L = D - self.A

        # 维度统一
        E = np.array([[1,0],[0,1]])
        self.L_big = np.kron(self.L, E)
        self.K_big = np.kron(self.K, E)

        # 存入位置与速度的轨迹
        self.xL_history = []
        self.vL_history = []
        self.X_history = []
        self.V_history = []

    def cal_error(self):
        '''计算误差'''
        X_error = self.X - self.xL - self.R
        V_error = self.V - self.vL
        return X_error.reshape(-1), V_error.reshape(-1)
    
    def cal(self):
        '''式4矩阵形式的代码表示'''
        X_error, V_error = self.cal_error()
        dX = self.V.reshape(-1)
        dV = -(self.L_big + self.K_big) @ X_error - (self.beta * self.L_big + self.gamma* self.K_big) @ V_error

        # 将(2n, 1)转化为(n,2)
        dX = dX.reshape(self.n, 2)
        dV = dV.reshape(self.n, 2)

        # 微分更新位置与速度
        self.X += dX * self.dt
        self.V += dV * self.dt

    def is_converged(self, step, tol = 5e-2):
        '''判断是否收敛并记录x与y方向收敛时间'''

        # 当前时间
        t = step * self.dt

        X_error, V_error = self.cal_error()
        X_error = X_error.reshape(self.n, 2)
        V_error = V_error.reshape(self.n, 2)

        # 与leader连接的车辆
        linked = np.diag(self.K) > 0

        X_err_linked = X_error[linked]
        V_err_linked = V_error[linked]

        # 分方向误差
        err_x = np.mean(np.abs(X_err_linked[:, 0]))
        err_y = np.mean(np.abs(X_err_linked[:, 1]))
        err_vx = np.mean(np.abs(V_err_linked[:, 0]))
        err_vy = np.mean(np.abs(V_err_linked[:, 1]))

        # 第一次达到阈值就记录
        if self.tx is None and err_x < tol:
            self.tx = t

        if self.ty is None and err_y < tol:
            self.ty = t

        if self.tvx is None and err_vx < tol:
            self.tvx = t

        if self.tvy is None and err_vy < tol:
            self.tvy = t

        # 位置与速度误差同时小于 tol 即收敛
        return (np.linalg.norm(X_error[linked]) < tol and
                np.linalg.norm(V_error[linked]) < tol)

    def run(self, steps):
        for step in range(steps):
            # 将位置与速度记录后更新
            self.xL_history.append(self.xL.copy())
            self.vL_history.append(self.vL.copy())
            self.X_history.append(self.X.copy())
            self.V_history.append(self.V.copy())
            self.cal()

            # 更新leader位置
            self.xL += self.vL * self.dt
            if(self.is_converged(step)):
                print(f"在第{step +1}次计算后收敛")
                time = (step + 1) * self.dt
                print(f"收敛时间: {time:.2f} s")
                break
        
        self.xL_history = np.array(self.xL_history)
        self.vL_history = np.array(self.vL_history)
        self.X_history = np.array(self.X_history)
        self.V_history = np.array(self.V_history)

class Data:
    '''存放数据'''
    def __init__(self, case = None, filepath = None):
        '''
        case 第几个数据
        filepath 文件路径
        无参数则手动输入
        '''
        self.case = case
        self.filepath = filepath
        if filepath and case:
            self.loadfiile()
        else:
            self.scanf()
    
    def loadfiile(self):
        '''读取json文件'''
        with open(self.filepath, 'r') as f:
            data_init = json.load(f)
        data = data_init[self.case - 1]
        # 基本参数
        self.n = data["n"]
        self.X_init = np.array(data["X_init"], dtype=float)
        self.V_init = np.array(data["V_init"], dtype=float)
        self.xL = np.array(data["leader"]["xL"], dtype=float)
        self.vL = np.array(data["leader"]["vL"], dtype=float)
        self.R = np.array(data["R"], dtype=float)
        self.A = np.array(data["A"], dtype=float)
        self.K = np.array(data["K"], dtype=float)
    
    def scanf(self):
        '''手动输入'''
        self.n = int(input("输入车辆数量 n: "))
        # 数组
        X, V, R, A = [], [], [], []

        # split 消除空格
        print("\n--- 输入每辆车的数据 ---")
        for i in range(self.n):
            print(f"\n车辆 {i}:")

            x = list(map(float, input("位置 x y: ").split()))
            v = list(map(float, input("速度 vx vy: ").split()))
            r = list(map(float, input("期望间距 rx ry: ").split()))

            X.append(x)
            V.append(v)
            R.append(r)
        self.X_init = np.array(X)
        self.V_init = np.array(V)
        self.R = np.array(R)

        print("\n--- 输入 Leader ---")
        self.xL = np.array(list(map(float, input("Leader位置 x y: ").split())))
        self.vL = np.array(list(map(float, input("Leader速度 vx vy: ").split())))

        print("\n--- 输入邻接矩阵 A ---")
        for i in range(self.n):
            row = list(map(float, input(f"A 第{i}行: ").split()))
            A.append(row)
        self.A = np.array(A)

        print("\n--- 输入 K---")
        k_diag = list(map(float, input("输入 k1 k2 ... kn: ").split()))
        self.K = np.diag(k_diag)

    def test(self):
        '''打印数据(测试)'''
        print("=== Data Summary ===")
        print(f"n = {self.n}")
        print("X_init:\n", self.X_init)
        print("V_init:\n", self.V_init)
        print("Leader:", self.xL, self.vL)
        print("K:", self.K)

class paint:
    '''
    绘制复现结果的类
    1 位置轨迹
    2 纵向间距轨迹
    3 横向间距轨迹
    4 x方向速度轨迹
    5 y方向速度轨迹
    '''

    def __init__(self, system):
        '''数据导入'''
        self.xL_hist = system.xL_history
        self.vL_hist = system.vL_history
        self.X_hist = system.X_history   
        self.V_hist = system.V_history
        self.t = np.arange(self.X_hist.shape[0]) * system.dt
        self.n = system.n
        self.xL = system.xL

    def fig1(self):
        '''位置轨迹'''
        plt.figure()
        plt.plot(self.xL_hist[:, 0], self.xL_hist[:, 1], 'r--', label='Leader')

        for i in range(self.n):
            # 绘制所有的 x 与 y 坐标
            plt.plot(self.X_hist[:, i, 0], self.X_hist[:, i, 1],
                     label='Vehicle i' if i == 0 else f'Vehicle i + {i}')

        plt.xlabel("X Position (m)")
        plt.ylabel("Y Position (m)")
        plt.title("The Position trajectories")
        plt.legend()
        plt.grid()
        plt.show()

    def fig2(self):
        '''纵向间距轨迹'''
        plt.figure()
        plt.plot(self.t, self.xL_hist[:, 0] - self.xL_hist[:, 0], 'r--', label='Leader')

        for i in range(self.n):
            gap_x = self.X_hist[:, i, 0] - self.xL_hist[:, 0]
            plt.plot(self.t, gap_x,
                     label='Vehicle i' if i == 0 else f'Vehicle i + {i}')

        plt.xlabel("Time (s)")
        plt.ylabel("Longitudinal Gap(m)")
        plt.title("The longitudinal gap")
        plt.legend()
        plt.grid()
        plt.show()
        
    def fig3(self):
        '''横向间距轨迹'''
        plt.figure()
        plt.plot(self.t, self.xL_hist[:, 1] - self.xL_hist[:, 1], 'r--', label='Leader')

        for i in range(self.n):
            gap_y = self.X_hist[:, i, 1] - self.xL_hist[:, 1]
            plt.plot(self.t, gap_y,
                     label='Vehicle i' if i == 0 else f'Vehicle i + {i}')

        plt.xlabel("Time (s)")
        plt.ylabel("Lateral Gap(m)")
        plt.title("The lateral gap")
        plt.legend()
        plt.grid()
        plt.show()

    def fig4(self):
        '''x方向速度轨迹'''
        plt.figure()
        plt.plot(self.t, self.vL_hist[:, 0], 'r--', label='Leader')

        for i in range(self.n):
            plt.plot(self.t, self.V_hist[:, i, 0],
                     label='Vehicle i' if i == 0 else f'Vehicle i + {i}')

        plt.xlabel("Time (s)")
        plt.ylabel("X-Velocity(m/s)")
        plt.title("x-velocity")
        plt.legend()
        plt.grid()
        plt.show()

    def fig5(self):
        '''y方向速度轨迹'''
        plt.figure()
        plt.plot(self.t, self.vL_hist[:, 1], 'r--', label='Leader')

        for i in range(self.n):
            plt.plot(self.t, self.V_hist[:, i, 1],
                     label='Vehicle i' if i == 0 else f'Vehicle i + {i}')

        plt.xlabel("Time (s)")
        plt.ylabel("Y-Velocity(m/s)")
        plt.title("y-velocity")
        plt.legend()
        plt.grid()
        plt.show()

def gif(system):
    X_hist = system.X_history
    xL_hist = np.array(system.xL_history)

    fig, ax = plt.subplots()

    def update(frame):
        '''每一帧都绘制一个画面'''
        # 每一帧都清空画面
        ax.clear()
        
        # 画车辆
        for i in range(system.n):
            # 画点
            ax.scatter(X_hist[frame, i, 0],
                       X_hist[frame, i, 1],
                       label='Vehicle i'if i == 0 else f'Vehicle i + {i}')
            # 画轨迹
            ax.plot(X_hist[:frame, i, 0],
            X_hist[:frame, i, 1])

        # 画 leader
        ax.scatter(xL_hist[frame, 0],
                   xL_hist[frame, 1],
                   c='red', label='Leader')
        ax.plot(xL_hist[:frame, 0],xL_hist[:frame, 1],'r--')
        
        # 坐标轴范围
        ax.set_xlim(0, 100)
        ax.set_ylim(0, 100)
        # 坐标轴标签
        ax.set_xlabel("X Position (m)")
        ax.set_ylabel("Y Position (m)")

        ax.set_title(f"Time: {frame * system.dt:.2f}s")
        ax.legend()

    ani = FuncAnimation(fig, update,
                        frames=range(0, len(X_hist), 3), # 根据收敛时间裁剪动画
                        interval=10)

    return ani

data = Data(1, "2026QG工作室人工智能组中期考核\作答\项目文件\code\data.json")

system = control_system(
    data.n,data.X_init,data.V_init,data.xL,
    data.vL,data.R,data.A, data.K, 
    beta= 1, gamma= 1, dt=0.01
)

system.run(steps=2000)

a = paint(system).fig1()

# ani = gif(system)
# ani.save("platoon.gif", writer='Pillow', fps=30)