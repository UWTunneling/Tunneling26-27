/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file           : main.c
  * @brief          : Main program body
  ******************************************************************************
  * @attention
  *
  * Copyright (c) 2022 STMicroelectronics.
  * All rights reserved.
  *
  * This software is licensed under terms that can be found in the LICENSE file
  * in the root directory of this software component.
  * If no LICENSE file comes with this software, it is provided AS-IS.
  *
  ******************************************************************************
  */
/* USER CODE END Header */
/* Includes ------------------------------------------------------------------*/
#include "main.h"
#include "cmsis_os.h"
#include "lwip.h"

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */
/* ETH_CODE: add lwiperf, see comment in StartDefaultTask function */
#include "lwip/apps/lwiperf.h"
/* Simple TCP echo server includes */
#include "lwip/tcp.h"
#include "lwip/netif.h"
#include <string.h>
/* USER CODE END Includes */

/* Private typedef -----------------------------------------------------------*/
/* USER CODE BEGIN PTD */

/* USER CODE END PTD */

/* Private define ------------------------------------------------------------*/
/* USER CODE BEGIN PD */

#ifndef HSEM_ID_0
#define HSEM_ID_0 (0U) /* HW semaphore 0*/
#endif
/* USER CODE END PD */

/* Private macro -------------------------------------------------------------*/
/* USER CODE BEGIN PM */

/* USER CODE END PM */

/* Private variables ---------------------------------------------------------*/

/* Definitions for defaultTask */
osThreadId_t defaultTaskHandle;
const osThreadAttr_t defaultTask_attributes = {
  .name = "defaultTask",
  .stack_size = 512 * 4,
  .priority = (osPriority_t) osPriorityNormal,
};
/* USER CODE BEGIN PV */

/* USER CODE END PV */

/* Private function prototypes -----------------------------------------------*/
void SystemClock_Config(void);
static void MPU_Config(void);
static void MX_GPIO_Init(void);
void StartDefaultTask(void *argument);

/* USER CODE BEGIN PFP */

/* USER CODE END PFP */

/* Private user code ---------------------------------------------------------*/
/* USER CODE BEGIN 0 */

/* Simple TCP Echo Server - listens on port 8080 and echoes back messages */
#define ECHO_PORT 8080

struct tcp_echo_state {
  struct tcp_pcb *pcb;
  char buffer[1024];
  int len;
  struct tcp_echo_state *next;  // For linked list of connections
};

// Global list of connected clients
static struct tcp_echo_state *connected_clients = NULL;

// LED control state (0 = off, 1 = on)
static volatile uint8_t green_led_state = 0;
static volatile uint8_t red_led_state = 0;
static volatile uint8_t yellow_led_state = 0;

// Handle LED commands from user input - toggle LEDs
static void handle_led_command(char cmd)
{
  switch(cmd) {
    case 'g':
    case 'G':
      // Green LED: toggle
      green_led_state = !green_led_state;
      HAL_GPIO_WritePin(GPIOB, GPIO_PIN_0, green_led_state ? GPIO_PIN_SET : GPIO_PIN_RESET);
      break;
      
    case 'r':
    case 'R':
      // Red LED: toggle
      red_led_state = !red_led_state;
      HAL_GPIO_WritePin(GPIOB, GPIO_PIN_14, red_led_state ? GPIO_PIN_SET : GPIO_PIN_RESET);
      break;
      
    case 'y':
    case 'Y':
      // Yellow LED: toggle
      yellow_led_state = !yellow_led_state;
      HAL_GPIO_WritePin(GPIOE, GPIO_PIN_1, yellow_led_state ? GPIO_PIN_SET : GPIO_PIN_RESET);
      break;
      
    default:
      // Unknown command, ignore
      break;
  }
}

static void remove_client(struct tcp_echo_state *es)
{
  // Remove from linked list
  if (connected_clients == es) {
    connected_clients = es->next;
  } else {
    struct tcp_echo_state *current = connected_clients;
    while (current != NULL && current->next != es) {
      current = current->next;
    }
    if (current != NULL) {
      current->next = es->next;
    }
  }
  mem_free(es);
}

static err_t tcp_echo_recv(void *arg, struct tcp_pcb *tpcb, struct pbuf *p, err_t err)
{
  struct tcp_echo_state *es = (struct tcp_echo_state *)arg;
  
  if (p == NULL) {
    // Connection closed
    if (es != NULL) {
      remove_client(es);
    }
    tcp_close(tpcb);
    return ERR_OK;
  }
  
  if (err != ERR_OK) {
    pbuf_free(p);
    return err;
  }
  
  // Copy received data
  pbuf_copy_partial(p, es->buffer, sizeof(es->buffer) - 1, 0);
  es->len = p->tot_len;
  es->buffer[es->len] = '\0';
  
  // Acknowledge received data
  tcp_recved(tpcb, p->tot_len);
  pbuf_free(p);
  
  // Check for LED commands (single character commands: g, r, y)
  if (es->len > 0) {
    char first_char = es->buffer[0];
    // Check if it's a single character command (with optional newline)
    if (es->len <= 2 && (first_char == 'g' || first_char == 'G' || 
                         first_char == 'r' || first_char == 'R' ||
                         first_char == 'y' || first_char == 'Y')) {
      handle_led_command(first_char);
    }
  }
  
  // Echo back the received data
  tcp_write(tpcb, es->buffer, es->len, TCP_WRITE_FLAG_COPY);
  tcp_output(tpcb);
  
  return ERR_OK;
}

static err_t tcp_echo_err(void *arg, err_t err)
{
  struct tcp_echo_state *es = (struct tcp_echo_state *)arg;
  if (es != NULL) {
    remove_client(es);
  }
  return ERR_OK;
}

static err_t tcp_echo_accept(void *arg, struct tcp_pcb *newpcb, err_t err)
{
  struct tcp_echo_state *es;
  
  if (err != ERR_OK || newpcb == NULL) {
    return ERR_VAL;
  }
  
  // Allocate state structure
  es = (struct tcp_echo_state *)mem_malloc(sizeof(struct tcp_echo_state));
  if (es == NULL) {
    tcp_close(newpcb);
    return ERR_MEM;
  }
  
  es->pcb = newpcb;
  es->len = 0;
  es->next = NULL;
  
  // Add to connected clients list
  es->next = connected_clients;
  connected_clients = es;
  
  // Set up callbacks
  tcp_arg(newpcb, es);
  tcp_recv(newpcb, tcp_echo_recv);
  //tcp_err(newpcb, tcp_echo_err);
  
  return ERR_OK;
}

static void send_ping_to_clients(void)
{
  struct tcp_echo_state *es = connected_clients;
  const char *ping_msg = "ping\n";
  int ping_len = strlen(ping_msg);
  
  LOCK_TCPIP_CORE();
  
  while (es != NULL) {
    if (es->pcb != NULL) {
      err_t err = tcp_write(es->pcb, ping_msg, ping_len, TCP_WRITE_FLAG_COPY);
      if (err == ERR_OK) {
        tcp_output(es->pcb);
      }
    }
    es = es->next;
  }
  
  UNLOCK_TCPIP_CORE();
}

static void tcp_echo_server_init(void)
{
  struct tcp_pcb *pcb;
  err_t err;
  
  // Create new TCP PCB
  pcb = tcp_new();
  if (pcb == NULL) {
    return;
  }
  
  // Bind to port 8080
  err = tcp_bind(pcb, IP_ADDR_ANY, ECHO_PORT);
  if (err != ERR_OK) {
    memp_free(MEMP_TCP_PCB, pcb);
    return;
  }
  
  // Listen for connections
  pcb = tcp_listen(pcb);
  if (pcb == NULL) {
    return;
  }
  
  // Set accept callback
  tcp_accept(pcb, tcp_echo_accept);
}

/* USER CODE END 0 */

/**
  * @brief  The application entry point.
  * @retval int
  */
int main(void)
{
  /* USER CODE BEGIN 1 */

  /* USER CODE END 1 */
/* USER CODE BEGIN Boot_Mode_Sequence_0 */

/* USER CODE END Boot_Mode_Sequence_0 */

  /* MPU Configuration--------------------------------------------------------*/
  MPU_Config();

  /* Enable I-Cache---------------------------------------------------------*/
  SCB_EnableICache();

  /* Enable D-Cache---------------------------------------------------------*/
  SCB_EnableDCache();

/* USER CODE BEGIN Boot_Mode_Sequence_1 */

/* USER CODE END Boot_Mode_Sequence_1 */
  /* MCU Configuration--------------------------------------------------------*/

  /* Reset of all peripherals, Initializes the Flash interface and the Systick. */
  HAL_Init();

  /* USER CODE BEGIN Init */

  /* USER CODE END Init */

  /* Configure the system clock */
  SystemClock_Config();
/* USER CODE BEGIN Boot_Mode_Sequence_2 */

/* USER CODE END Boot_Mode_Sequence_2 */

  /* USER CODE BEGIN SysInit */

  /* USER CODE END SysInit */

  /* Initialize all configured peripherals */
  MX_GPIO_Init();
  /* USER CODE BEGIN 2 */
  /* ETH_CODE: fixed core synchronization
   * Release M4 core after GPIO and peripherals init
   * to avoid conflict.
   */
  __HAL_RCC_HSEM_CLK_ENABLE();
  HAL_HSEM_FastTake(HSEM_ID_0);
  HAL_HSEM_Release(HSEM_ID_0,0);
  /* USER CODE END 2 */

  /* Init scheduler */
  osKernelInitialize();

  /* USER CODE BEGIN RTOS_MUTEX */
  /* add mutexes, ... */
  /* USER CODE END RTOS_MUTEX */

  /* USER CODE BEGIN RTOS_SEMAPHORES */
  /* add semaphores, ... */
  /* USER CODE END RTOS_SEMAPHORES */

  /* USER CODE BEGIN RTOS_TIMERS */
  /* start timers, add new ones, ... */
  /* USER CODE END RTOS_TIMERS */

  /* USER CODE BEGIN RTOS_QUEUES */
  /* add queues, ... */
  /* USER CODE END RTOS_QUEUES */

  /* Create the thread(s) */
  /* creation of defaultTask */
  defaultTaskHandle = osThreadNew(StartDefaultTask, NULL, &defaultTask_attributes);

  /* USER CODE BEGIN RTOS_THREADS */
  /* add threads, ... */
  /* USER CODE END RTOS_THREADS */

  /* USER CODE BEGIN RTOS_EVENTS */
  /* add events, ... */
  /* USER CODE END RTOS_EVENTS */

  /* Start scheduler */
  osKernelStart();

  /* We should never get here as control is now taken by the scheduler */
  /* Infinite loop */
  /* USER CODE BEGIN WHILE */
  while (1)
  {
    /* USER CODE END WHILE */

    /* USER CODE BEGIN 3 */
  }
  /* USER CODE END 3 */
}

/**
  * @brief System Clock Configuration
  * @retval None
  */
void SystemClock_Config(void)
{
  RCC_OscInitTypeDef RCC_OscInitStruct = {0};
  RCC_ClkInitTypeDef RCC_ClkInitStruct = {0};

  /** Supply configuration update enable
  */
  HAL_PWREx_ConfigSupply(PWR_DIRECT_SMPS_SUPPLY);

  /** Configure the main internal regulator output voltage
  */
  __HAL_PWR_VOLTAGESCALING_CONFIG(PWR_REGULATOR_VOLTAGE_SCALE1);

  while(!__HAL_PWR_GET_FLAG(PWR_FLAG_VOSRDY)) {}

  /** Initializes the RCC Oscillators according to the specified parameters
  * in the RCC_OscInitTypeDef structure.
  */
  RCC_OscInitStruct.OscillatorType = RCC_OSCILLATORTYPE_HSI;
  RCC_OscInitStruct.HSIState = RCC_HSI_DIV1;
  RCC_OscInitStruct.HSICalibrationValue = RCC_HSICALIBRATION_DEFAULT;
  RCC_OscInitStruct.PLL.PLLState = RCC_PLL_ON;
  RCC_OscInitStruct.PLL.PLLSource = RCC_PLLSOURCE_HSI;
  RCC_OscInitStruct.PLL.PLLM = 32;
  RCC_OscInitStruct.PLL.PLLN = 400;
  RCC_OscInitStruct.PLL.PLLP = 2;
  RCC_OscInitStruct.PLL.PLLQ = 2;
  RCC_OscInitStruct.PLL.PLLR = 2;
  RCC_OscInitStruct.PLL.PLLRGE = RCC_PLL1VCIRANGE_1;
  RCC_OscInitStruct.PLL.PLLVCOSEL = RCC_PLL1VCOWIDE;
  RCC_OscInitStruct.PLL.PLLFRACN = 0;
  if (HAL_RCC_OscConfig(&RCC_OscInitStruct) != HAL_OK)
  {
    Error_Handler();
  }

  /** Initializes the CPU, AHB and APB buses clocks
  */
  RCC_ClkInitStruct.ClockType = RCC_CLOCKTYPE_HCLK|RCC_CLOCKTYPE_SYSCLK
                              |RCC_CLOCKTYPE_PCLK1|RCC_CLOCKTYPE_PCLK2
                              |RCC_CLOCKTYPE_D3PCLK1|RCC_CLOCKTYPE_D1PCLK1;
  RCC_ClkInitStruct.SYSCLKSource = RCC_SYSCLKSOURCE_PLLCLK;
  RCC_ClkInitStruct.SYSCLKDivider = RCC_SYSCLK_DIV1;
  RCC_ClkInitStruct.AHBCLKDivider = RCC_HCLK_DIV2;
  RCC_ClkInitStruct.APB3CLKDivider = RCC_APB3_DIV2;
  RCC_ClkInitStruct.APB1CLKDivider = RCC_APB1_DIV2;
  RCC_ClkInitStruct.APB2CLKDivider = RCC_APB2_DIV2;
  RCC_ClkInitStruct.APB4CLKDivider = RCC_APB4_DIV2;

  if (HAL_RCC_ClockConfig(&RCC_ClkInitStruct, FLASH_LATENCY_2) != HAL_OK)
  {
    Error_Handler();
  }
}

/**
  * @brief GPIO Initialization Function
  * @param None
  * @retval None
  */
static void MX_GPIO_Init(void)
{
  GPIO_InitTypeDef GPIO_InitStruct = {0};

  /* GPIO Ports Clock Enable */
  __HAL_RCC_GPIOC_CLK_ENABLE();
  __HAL_RCC_GPIOH_CLK_ENABLE();
  __HAL_RCC_GPIOA_CLK_ENABLE();
  __HAL_RCC_GPIOB_CLK_ENABLE();
  __HAL_RCC_GPIOD_CLK_ENABLE();
  __HAL_RCC_GPIOG_CLK_ENABLE();
  __HAL_RCC_GPIOE_CLK_ENABLE();

  /*Configure GPIO pins : PD8 PD9 */
  GPIO_InitStruct.Pin = GPIO_PIN_8|GPIO_PIN_9;
  GPIO_InitStruct.Mode = GPIO_MODE_AF_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
  GPIO_InitStruct.Alternate = GPIO_AF7_USART3;
  HAL_GPIO_Init(GPIOD, &GPIO_InitStruct);

  /*Configure GPIO pins : PA8 PA11 PA12 */
  GPIO_InitStruct.Pin = GPIO_PIN_8|GPIO_PIN_11|GPIO_PIN_12;
  GPIO_InitStruct.Mode = GPIO_MODE_AF_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
  GPIO_InitStruct.Alternate = GPIO_AF10_OTG1_FS;
  HAL_GPIO_Init(GPIOA, &GPIO_InitStruct);

  /*Configure GPIO pin : PE1 - Yellow LED */
  GPIO_InitStruct.Pin = GPIO_PIN_1;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
  HAL_GPIO_Init(GPIOE, &GPIO_InitStruct);
  HAL_GPIO_WritePin(GPIOE, GPIO_PIN_1, GPIO_PIN_RESET); // Start with LED off

  /*Configure GPIO pin : PB0 - Green LED */
  GPIO_InitStruct.Pin = GPIO_PIN_0;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
  HAL_GPIO_Init(GPIOB, &GPIO_InitStruct);
  HAL_GPIO_WritePin(GPIOB, GPIO_PIN_0, GPIO_PIN_RESET); // Start with LED off

  /*Configure GPIO pin : PB14 - Red LED */
  GPIO_InitStruct.Pin = GPIO_PIN_14;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
  HAL_GPIO_Init(GPIOB, &GPIO_InitStruct);
  HAL_GPIO_WritePin(GPIOB, GPIO_PIN_14, GPIO_PIN_RESET); // Start with LED off

}

/* USER CODE BEGIN 4 */

/* USER CODE END 4 */

/* USER CODE BEGIN Header_StartDefaultTask */
/**
  * @brief  Function implementing the defaultTask thread.
  * @param  argument: Not used
  * @retval None
  */
/* USER CODE END Header_StartDefaultTask */
void StartDefaultTask(void *argument)
{
  /* init code for LWIP */
  MX_LWIP_Init();
  
  /* Wait for network interface to be up */
  osDelay(1000);
  
  /* USER CODE BEGIN 5 */
  // Test LED - flash 3 times quickly to confirm GPIO is working
  for(int i = 0; i < 6; i++) {
    HAL_GPIO_TogglePin(GPIOE, GPIO_PIN_1);
    osDelay(100);
  }
  HAL_GPIO_WritePin(GPIOE, GPIO_PIN_1, GPIO_PIN_RESET); // Ensure LED starts off
  osDelay(500);
  
  /* ETH_CODE: Adding lwiperf to measure TCP/IP performance.
     * iperf 2.0.6 (or older?) is required for the tests. Newer iperf2 versions
     * might work without data check, but they send different headers.
     * iperf3 is not compatible at all.
     * Adding lwiperf.c file to the project is necessary.
     * The default include path should already contain
     * 'lwip/apps/lwiperf.h'
     */
    LOCK_TCPIP_CORE();
    
    // Start simple TCP echo server on port 8080
    tcp_echo_server_init();
    
    // Optional: Keep iperf for performance testing
    // lwiperf_start_tcp_server_default(NULL, NULL);
    // ip4_addr_t remote_addr;
    // IP4_ADDR(&remote_addr, 192, 168, 1, 1);
    // lwiperf_start_tcp_client_default(&remote_addr, NULL, NULL);
    
    UNLOCK_TCPIP_CORE();
    
    uint32_t ping_counter = 0;
    
    /* Infinite loop */
    for(;;)
    {
      // Send ping every 1 second (every 2 iterations since we delay 500ms)
      ping_counter++;
      if (ping_counter >= 2) {
        send_ping_to_clients();
        ping_counter = 0;
      }
      
      osDelay(500);
    }
  /* USER CODE END 5 */
}

/* MPU Configuration */

void MPU_Config(void)
{
  MPU_Region_InitTypeDef MPU_InitStruct = {0};

  /* Disables the MPU */
  HAL_MPU_Disable();

  /** Initializes and configures the Region and the memory to be protected
  */
  MPU_InitStruct.Enable = MPU_REGION_ENABLE;
  MPU_InitStruct.Number = MPU_REGION_NUMBER0;
  MPU_InitStruct.BaseAddress = 0x0;
  MPU_InitStruct.Size = MPU_REGION_SIZE_4GB;
  MPU_InitStruct.SubRegionDisable = 0x87;
  MPU_InitStruct.TypeExtField = MPU_TEX_LEVEL0;
  MPU_InitStruct.AccessPermission = MPU_REGION_NO_ACCESS;
  MPU_InitStruct.DisableExec = MPU_INSTRUCTION_ACCESS_DISABLE;
  MPU_InitStruct.IsShareable = MPU_ACCESS_SHAREABLE;
  MPU_InitStruct.IsCacheable = MPU_ACCESS_NOT_CACHEABLE;
  MPU_InitStruct.IsBufferable = MPU_ACCESS_NOT_BUFFERABLE;

  HAL_MPU_ConfigRegion(&MPU_InitStruct);

  /** Initializes and configures the Region and the memory to be protected
  */
  MPU_InitStruct.Number = MPU_REGION_NUMBER1;
  MPU_InitStruct.BaseAddress = 0x30020000;
  MPU_InitStruct.Size = MPU_REGION_SIZE_128KB;
  MPU_InitStruct.SubRegionDisable = 0x0;
  MPU_InitStruct.TypeExtField = MPU_TEX_LEVEL1;
  MPU_InitStruct.AccessPermission = MPU_REGION_FULL_ACCESS;
  MPU_InitStruct.IsShareable = MPU_ACCESS_NOT_SHAREABLE;

  HAL_MPU_ConfigRegion(&MPU_InitStruct);

  /** Initializes and configures the Region and the memory to be protected
  */
  MPU_InitStruct.Number = MPU_REGION_NUMBER2;
  MPU_InitStruct.BaseAddress = 0x30040000;
  MPU_InitStruct.Size = MPU_REGION_SIZE_512B;
  MPU_InitStruct.TypeExtField = MPU_TEX_LEVEL0;
  MPU_InitStruct.IsShareable = MPU_ACCESS_SHAREABLE;
  MPU_InitStruct.IsBufferable = MPU_ACCESS_BUFFERABLE;

  HAL_MPU_ConfigRegion(&MPU_InitStruct);
  /* Enables the MPU */
  HAL_MPU_Enable(MPU_PRIVILEGED_DEFAULT);

}

/**
  * @brief  Period elapsed callback in non blocking mode
  * @note   This function is called  when TIM6 interrupt took place, inside
  * HAL_TIM_IRQHandler(). It makes a direct call to HAL_IncTick() to increment
  * a global variable "uwTick" used as application time base.
  * @param  htim : TIM handle
  * @retval None
  */
void HAL_TIM_PeriodElapsedCallback(TIM_HandleTypeDef *htim)
{
  /* USER CODE BEGIN Callback 0 */

  /* USER CODE END Callback 0 */
  if (htim->Instance == TIM6) {
    HAL_IncTick();
  }
  /* USER CODE BEGIN Callback 1 */

  /* USER CODE END Callback 1 */
}

/**
  * @brief  This function is executed in case of error occurrence.
  * @retval None
  */
void Error_Handler(void)
{
  /* USER CODE BEGIN Error_Handler_Debug */
  /* User can add his own implementation to report the HAL error return state */
  __disable_irq();
  while (1)
  {
  }
  /* USER CODE END Error_Handler_Debug */
}

#ifdef  USE_FULL_ASSERT
/**
  * @brief  Reports the name of the source file and the source line number
  *         where the assert_param error has occurred.
  * @param  file: pointer to the source file name
  * @param  line: assert_param error line source number
  * @retval None
  */
void assert_failed(uint8_t *file, uint32_t line)
{
  /* USER CODE BEGIN 6 */
  /* User can add his own implementation to report the file name and line number,
     ex: printf("Wrong parameters value: file %s on line %d\r\n", file, line) */
  /* USER CODE END 6 */
}
#endif /* USE_FULL_ASSERT */
