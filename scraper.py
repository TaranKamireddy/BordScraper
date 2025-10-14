from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.options import Options
from datetime import datetime, timedelta, timezone, date, time
from time import sleep
import json
import base64
import requests
import asyncio
import pytz

DELAY = 0 #delay for loading pages
LOAD_MORE = 2 #number of times to load more events
GROUP = "Purdue" #change between "Purdue" and "IU"
DAYS_TO_SEARCH = 31

wd_options = Options()
# wd_options.add_argument("-headless")

req_headers = {
  'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.10 Safari/605.1.1',
}

# manual input
# search_date = {"Month":11, "Day":24, "Year":2025}
# startDate = date(search_date["Month"], search_date["Day"], search_date["Year"])

# automatic input
startDate = date.today()

#verify date is in search range
endDate = startDate + timedelta(days=DAYS_TO_SEARCH,weeks=0)
startUTC = datetime.combine(startDate, time(0,0,0)).replace(tzinfo=timezone.utc).timestamp() * 1000
endUTC = datetime.combine(endDate, time(0,0,0)).replace(tzinfo=timezone.utc).timestamp() * 1000

#query to search
query = 'Indianapolis'

#route to post events to
# route = 'https://localhost:8000/'
route = 'https://campusbord.com/api/event/batch_upload'

#converts categories and perks to tags
catperkToTag = {
  "Free Food": "Food",
  "Education": "Academic",
  "Festival/Celebration": "Social",
  "Recreation/Athletic": "Sport",
  "Social": "Social",
  "Conference": "Career",
  "Callout": "Social",
  "Athletic Contest/Sporting Event": "Sport",
  "Food Fundraiser": "Food",
  "Training/Workshop/Learning Opportunity": "Career"
}

def main():
  driver = webdriver.Firefox(options=wd_options)

  iuspot = f'https://thespot.iupui.edu/events?customdate={startDate.strftime("%a %b %d %Y")} 00%3A00%3A00 GMT-0500&query={query}'
  purdue = f'https://boilerlink.purdue.edu/events?customdate={startDate.strftime("%a %b %d %Y")} 00%3A00%3A00 GMT-0500&query={query}'

  link = purdue if GROUP == 'Purdue' else iuspot

  print(f'Searching through: {link}')

  #load event page
  driver.get(link)

  #loads more events
  for _ in range(LOAD_MORE):
    button = driver.find_elements(By.XPATH, "//div[@class='outlinedButton']//button")[-1]
    if button.text == "LOAD MORE":
      button.click()
      sleep(5)
    else:
      break

  #find event div
  foundEventList = False
  while not foundEventList:
    try:
      eventDiv = driver.find_element(By.ID, 'event-discovery-list') #div id with all the events
      foundEventList = True
    except:
      print("Event list not loaded yet, trying again in 1 second")
      sleep(1)

  #finds all event links
  children = eventDiv.find_elements(By.XPATH, './*')[0].find_elements(By.XPATH, './*') #gets children from the div
  links = [child.find_element(By.TAG_NAME, 'a').get_attribute('href') for child in children]

  #closes page
  driver.quit()

  print(f'Number of events found: {len(links)}\n')
  print(f'Event links: \n{"\n".join(links)}')

  #parse event links
  events = parseLinks(links)

  #format events
  events = formatEvents(events)

  #send events to route
  asyncio.run(postEvents(events))

  #write output to json (for debugging)
  writeOutput(events)

async def postEvents(events):
  sizeLimit = 15
  for i in range(len(events["events"])//sizeLimit + 1):
    batch = {"events": events["events"][i*sizeLimit:(i+1)*sizeLimit]}
    writeOutput(batch,f"batch{i}.json")
    try:
      req = requests.post(route, headers=req_headers, json=batch)#, verify='campusbord-com-chain.pem')
      try:
        print(f"\n{req.json()}")
      except:
        print(f"\n{req.status_code},  {req.reason}, {req.headers}")
    except:
      print("\nRequest Failed")

def parseLinks(links):
  events = {"events": []}

  for link in links:
    print(f'\nParsing through: {link}')
    driver = webdriver.Firefox(options=wd_options)
    driver.get(link)
    sleep(DELAY)

    #loading raw content
    loadedAll = False
    failedToLoad = 0
    while not loadedAll:
      if failedToLoad >= 3:
        break

      try:
        raw = driver.find_element(By.XPATH, './*').text.split("\n")
        image = driver.find_element(By.CSS_SELECTOR, "div[role='img']")
        imageurl = image.value_of_css_property("background-image")[5:-2]
        searchFor = ["Description", "SIGN IN", "Location", "Date and Time"]
        for item in searchFor:
          raw.index(item)
        if "Host Organization" in raw or "Host Organizations" in raw:
          loadedAll = True
        else:
          raw.index("Host Organization")
      except:
        failedToLoad += 1
        print("All data not loaded yet, trying again in 1 second")
        sleep(1)

    #close page
    driver.quit()

    #event didn't load properly
    if failedToLoad >= 3:
      "Event didn't load properly or is missing information"
      continue

    #parse data
    raw = [data.strip() for data in raw]

    # print(raw)

    endIndx = raw.index("Description") + 1
    while raw[endIndx] not in {"RSVP to Event", "Perks", "Host Organization", "Host Organizations", "Categories"}:
      endIndx += 1
    description = "\n".join(raw[raw.index("Description") + 1:endIndx])
    title = raw[raw.index("SIGN IN") + 1]
    location = raw[raw.index("Location") + 1:raw.index("Description")]
    if location[-1] == "View Map":
      location = location[:-1]
    location = "\n".join(location)
    start = toUnix(raw[raw.index("Date and Time") + 1][:-7])
    end = toUnix(raw[raw.index("Date and Time") + 2][:-4])
    if (multorgs := ("Host Organizations" in raw)):
      if "Other events hosted by these organizations" in raw:
        orgs = "\n".join(raw[raw.index("Host Organizations") + 1:raw.index("Other events hosted by these organizations")])
      else:
        orgs = "\n".join([org for org in raw[raw.index("Host Organizations") + 1:] if len(org) > 2])
    else:
      orgs = raw[raw.index("Host Organization") + 1]
      if len(orgs) == 1:
        orgs = raw[raw.index("Host Organization") + 2]
    hostidx = "Host Organizations" if multorgs else "Host Organization"
    if "Categories" in raw:
      categories = raw[raw.index("Categories") + 1:raw.index(hostidx)]
    else:
      categories = []
    if "Perks" in raw:
      if "Categories" in raw:
        perks = raw[raw.index("Perks") + 1:raw.index("Categories")]
      else:
        perks = raw[raw.index("Perks") + 1:raw.index(hostidx)]
    else:
      perks = []
    email = "scraper@scraper.com"

    #hardcoded some stuff but dw bout this
    for message in ["SIGN IN TO RSVP", "RSVP to Event"]:
      if message in categories:
        categories.remove(message)
      if message in perks:
        perks.remove(message)

    data = {
      "title": title,
      "org": orgs,
      "loc": location,
      "desc": description,
      "startTime": start,
      "endTime": end,
      "email": email,
      "categories": categories,
      "perks": perks,
      "image": imageurl,
    }

    # print(data)
    print(title)

    #verify event in search range (could remove to just allow everything)
    if start < startUTC or start > endUTC:
      print("Event skipped because it's not in time range")
      continue

    events["events"].append(data)

  return events

def formatEvents(events):
  for event in events["events"]:
    event["tags"] = set()

    #convert catergories to tags
    for tag in event["categories"]:
      if tag in catperkToTag:
        event["tags"].add(catperkToTag[tag])

    event["tags"] = list(event["tags"])

    if not event["tags"]:
      event["tags"].append(GROUP)

    del event["categories"]
    del event["perks"]

    #default image
    if "campuslabsengage" in event["image"]:
      del event["image"]
    else:
    #not default image
      req = requests.get(event['image'])
      b64 = base64.b64encode(req._content).decode('ASCII')
      event['image'] = b64
      event['imageType'] = req.headers['Content-Type']

  return events

def toUnix(date):
  formatDate = "%A, %B %d %Y at %I:%M %p"
  formatDate = datetime.strptime(date, formatDate)
  timezone = pytz.timezone("US/Eastern")
  formatDate = timezone.localize(formatDate)
  unix = int(formatDate.timestamp()) * 1000

  return unix

def writeOutput(events, file="events.json"):
  with open(file, "w") as f:
    f.write(json.dumps(events, indent=2))

if __name__ == "__main__":
  main()