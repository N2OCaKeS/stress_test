#!/bin/bash

GNUPLOT_BIN=/usr/bin/gnuplot
TEMPLATES_PATH="${PWD}/templates/"
CSV_TEMPLATE_FILE="${TEMPLATES_PATH}/percentages.tpl"
PLOT_TEMPLATE_FILE="${TEMPLATES_PATH}/values.tpl"
MERGE_VALUES_TEMPLATE_FILE="${TEMPLATES_PATH}/merged_values.tpl"
MERGE_PERCENTAGES_TEMPLATE_FILE="${TEMPLATES_PATH}/merged_percentages.tpl"
# Prepare ab parameters
RESULTS_PATH="${PWD}/report/$1"
CSV_RESULTS_FILE="${RESULTS_PATH}/percentages.csv"
PLOT_FILE="${RESULTS_PATH}/values.tsv"
AB_OUTPUT_FILE="${RESULTS_PATH}/summary.txt"

function render_template() {
  template_file=${1}
  result_file=${2}

  eval "echo \"$(cat ${template_file})\"" > "${result_file}"

  echo ${result_file}
}

##### Plot results
# Render values template
cd ${RESULTS_PATH} || exit
CONCURRENCY="$(cat summary.txt | grep 'Concurrency' | awk '{print $3}')"
echo -e "Plotting values results..."

# Define plot lines
PLOT_LINES="\"${PLOT_FILE}\" using 9 smooth sbezier with lines title \"${HOSTNAME}\""
IMAGE_FILE="$(basename ${PLOT_FILE})"
rendered_values_template=$(render_template ${PLOT_TEMPLATE_FILE} "${RESULTS_PATH}/values.p")

# Plot results
GNUPLOT_COMMAND="${GNUPLOT_BIN} ${rendered_values_template}"
echo -e "\n${GNUPLOT_COMMAND}\n"
${GNUPLOT_COMMAND}
echo -e "Done."

# Render percentages template
echo -e "Plotting percentages results..."
# Remove header line
sed 1d ${CSV_RESULTS_FILE} > ${CSV_RESULTS_FILE}.fixed
PLOT_LINES="\"${CSV_RESULTS_FILE}.fixed\"  with lines title \"${HOSTNAME}\""
IMAGE_FILE="$(basename ${CSV_RESULTS_FILE})"
rendered_percentages_template=$(render_template ${CSV_TEMPLATE_FILE} "${RESULTS_PATH}/percentages.p")

# Plot results
GNUPLOT_COMMAND="${GNUPLOT_BIN} ${rendered_percentages_template}"
echo -e "\n${GNUPLOT_COMMAND}\n"
${GNUPLOT_COMMAND}
echo -e "Done."

cd .. || exit

