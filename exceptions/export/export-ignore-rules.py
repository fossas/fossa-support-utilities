import requests
import json
import csv
import argparse

# Define the base URL with placeholders for parameters
base_url = 'https://app.fossa.com/api/v2/issues/exceptions?filters[category]={category}&page={page}&count={count}'

def fetch_data(category, count, access_token):
    headers = {
        'accept': 'application/json',
        'authorization': f'Bearer {access_token}',
    }

    results = []
    page = 1

    while True:
        url = base_url.format(category=category, page=page, count=count)
        response = requests.get(url, headers=headers)

        if response.status_code == 200:
            data = response.json()
            print(data)
            print('paginate: ' + str(page))
            if data and 'exceptions' in data:
                items = data['exceptions']
                if not items:
                    # If there are no items, break out of the loop
                    break
                results.extend(items)
                page += 1
            else:
                break
        else:
            print(f"Failed to fetch page {page}: Status Code {response.status_code}")
            break

    return results

def main():
    parser = argparse.ArgumentParser(description='Fetch and save data from a FOSSA API endpoint.')
    parser.add_argument('access_token', type=str, help='FOSSA API Bearer token')
    parser.add_argument('--category', type=str, default='licensing', help='Category filter [licensing, security]')
    parser.add_argument('--count', type=int, default=1000, help='Number of items per page')
    parser.add_argument('--output', type=str, choices=['json', 'csv'], default='json', help='Output format [csv, json]')

    args = parser.parse_args()

    results = fetch_data(args.category, args.count, args.access_token)

    if args.output == 'json':
        # Save the results to a JSON file
        with open('paginated_results.json', 'w') as json_file:
            json.dump(results, json_file, indent=2)
        print(f"Pagination complete. {len(results)} items retrieved and saved to 'paginated_results.json'.")
    elif args.output == 'csv':
        # Convert results to CSV
        csv_filename = 'paginated_results.csv'
        with open(csv_filename, 'w', newline='') as csv_file:
            csv_writer = csv.DictWriter(csv_file, fieldnames=results[0].keys())
            csv_writer.writeheader()
            csv_writer.writerows(results)
        print(f"Pagination complete. {len(results)} items converted and saved to 'paginated_results.csv'.")
    else:
        print("Invalid output format. Choose 'json' or 'csv'.")

if __name__ == '__main__':
    main()
